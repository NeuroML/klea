#!/usr/bin/env python3
"""
Retrieve information node

File: rag_pkg/klea_rag/nodes/retrieve_info.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Hashable
from typing import Any, override

from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)
from klea_utils.stores.config import FilterFieldInfo
from klea_utils.stores.filters import restrict_metadata_filter
from klea_utils.stores.retrieval.base import BaseKleaRetriever
from klea_utils.stores.utils import (
    normalize_text,
    rerank_by_recency,
    rrf_merge,
    truncate_reference_material,
)

from klea_rag.schemas import RAGState


def _format_scores(doc: Any, score: float, precision: int = 2) -> str:
    """Format a document's blended score for the reference material display.

    The score carried in the tuple is the final recency-blended score (see
    ``klea_utils.stores.utils.rerank_by_recency``).

    :param doc: Document (retained for a stable signature)
    :param score: Blended relevance/recency score for *doc*
    :param precision: Number of decimal places
    :returns: Display string, e.g. ``[0.87]``
    """
    return f"[{score:.{precision}f}]"


class RetrieveInfoNode(AbstractLangGraphNode[RAGState, dict[str, Any]]):
    """Retrieve reference material from the configured retrievers.

    Queries all retrievers (vector stores, BM25 stores) for the domains in
    the ``query_domains`` list using the same retrieval query, fuses the
    results with Reciprocal Rank Fusion, and keeps the references ranked
    best by RRF up to a global character budget (``max_refs_size``).
    Optionally increments k when asked to retrieve more info.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        retrievers: list[BaseKleaRetriever] | None = None,
        max_refs_size: int = 20000,
        filter_fields_by_domain: dict[str, list[FilterFieldInfo]] | None = None,
    ):
        """Initialise the retrieval node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param retrievers: Retrievers to query (empty list skips retrieval)
        :param max_refs_size: Global character budget for the reference
            material fed to the answer LLM (see
            ``klea_utils.stores.utils.truncate_reference_material``)
        :param filter_fields_by_domain: ``{domain: [FilterFieldInfo]}``
            configured for each domain.  When set, the combined metadata
            filter is restricted per domain to the clauses on that
            domain's declared fields, so a cross-domain query never
            applies one domain's filter fields to another domain's
            retrievers.  When empty (not configured) the combined filter
            is passed through unchanged.
        """
        super().__init__(logger, label)
        self.retrievers = retrievers or []
        self.max_refs_size = max_refs_size
        self.filter_fields_by_domain: dict[str, list[FilterFieldInfo]] = (
            filter_fields_by_domain or {}
        )

    def _filter_for_domain(
        self, metadata_filter: dict[str, Any] | None, domain_name: str
    ) -> dict[str, Any] | None:
        """Restrict *metadata_filter* to the fields *domain_name* declares.

        Without a per-domain configuration (the map is empty) the combined
        filter is passed through unchanged.  Once configured, a domain not
        present in the map is treated as declaring no filter fields, so
        every clause is dropped rather than applied to a domain that never
        declared it.
        """
        if not self.filter_fields_by_domain:
            return metadata_filter
        fields = self.filter_fields_by_domain.get(domain_name, [])
        return restrict_metadata_filter(
            metadata_filter, {field.name for field in fields}
        )

    @override
    async def execute(self, state: RAGState) -> dict[str, Any]:
        """Retrieve and rank reference material."""
        # Every pass through this node is one retrieval attempt (the initial
        # query retrieval, retrieve_more_info k-increases, and modify_query
        # re-retrievals), so the evaluator loop budgets against a single
        # combined counter instead of per-kind counters.
        retrieval_attempts = state.retrieval_attempts + 1

        if not self.retrievers:
            if any(d != "undefined" for d in state.query_domains):
                self.logger.warning(
                    f"No retrievers configured for domains {state.query_domains}, skipping retrieval"
                )
            else:
                self.logger.debug("No retrievers configured, skipping retrieval")
            return {"retrieval_attempts": retrieval_attempts}

        self.write_custom_stream({"type": "progress", "node": self.label})

        reference_material = dict(state.reference_material)
        # Apply the same normalization used at indexing time so query and
        # stored chunks share an identical plain-text form (see
        # klea_utils.stores.utils.normalize_text).  The query is LLM-generated
        # so artifacts are rare, but this makes the invariant explicit.
        raw_query = state.retrieval_query.search_query
        cleaned_query = normalize_text(raw_query)
        self.logger.debug(f"{raw_query = }\n{cleaned_query = }")
        if not cleaned_query.strip():
            self.logger.warning(
                "Empty retrieval query after normalization, skipping retrieval"
            )
            return {
                "retrieval_attempts": retrieval_attempts,
                "reference_material": reference_material,
            }

        # Check if evaluator requested more info
        if state.text_response_eval.next_step == "retrieve_more_info":
            for retriever in self.retrievers:
                grew = retriever.inc_k()
                if not grew:
                    self.logger.debug(
                        f"{retriever.source_label} k already at max, not grown"
                    )

        # Retrieve from all retrievers for all domains
        metadata_filter = state.retrieval_query.to_metadata_filter()
        self.logger.debug(f"{metadata_filter = }")
        for domain_name in state.query_domains:
            # Skip undefined domain
            if domain_name == "undefined":
                continue

            # A cross-domain query shares one generated filter; each domain
            # only receives the clauses on the fields it declares.
            domain_filter = self._filter_for_domain(metadata_filter, domain_name)
            self.logger.debug(
                f"{domain_name = } got {domain_filter = } (from {metadata_filter = })"
            )

            result_sets: list[tuple[str, list[tuple[Any, float]]]] = []
            for retriever in self.retrievers:
                try:
                    docs = retriever.retrieve(
                        domain_name=domain_name,
                        query=cleaned_query,
                        metadata_filter=domain_filter,
                    )
                except Exception as exc:  # noqa: BLE001   ---  per-retriever isolation, other retrievers still contribute
                    self.logger.warning(
                        f"Retriever {retriever.source_label} failed for {domain_name}: {exc}"
                    )
                    continue
                result_sets.append((retriever.source_label, docs))
            if not result_sets:
                self.logger.warning(
                    f"All retrievers failed for domain {domain_name}, skipping"
                )
                reference_material[domain_name] = []
                continue
            merged = rrf_merge(result_sets)
            reference_material[domain_name] = rerank_by_recency(merged)

        reference_material = truncate_reference_material(
            reference_material, max_chars=self.max_refs_size
        )

        self.logger.debug(f"{reference_material =}")

        # Emit inspection event: counts plus the retrieved material
        per_domain_counts = {
            domain: len(docs) for domain, docs in reference_material.items()
        }
        total_docs = sum(per_domain_counts.values())
        inspect_data = NodeStreamData(
            heading="Document Retrieval",
            summary=f"Retrieved {total_docs} documents from {len(per_domain_counts)} domains",
            details={
                "per_domain_counts": per_domain_counts,
                "reference_material": {
                    domain: [
                        {
                            "content": doc.page_content,
                            "metadata": doc.metadata,
                            "score": score,
                        }
                        for doc, score in docs
                    ]
                    for domain, docs in reference_material.items()
                },
            },
        )
        inspect_event = NodeStreamEvent(
            type="inspect", node=self.label, data=inspect_data
        )
        self.write_custom_stream(inspect_event.model_dump())

        # Emit state event
        # URLs, file name, score, not full content
        md_lines = []
        for domain, docs in reference_material.items():
            if not docs:
                continue

            md_lines.append(f"### {domain}\n")

            seen: dict[Hashable, tuple[Any, float, list[str]]] = {}
            for doc, score in sorted(docs, key=lambda x: x[1], reverse=True):
                url_items = [
                    (k, v)
                    for k, v in doc.metadata.items()
                    if k.startswith("url") and isinstance(v, str)
                ]
                file_name = doc.metadata.get("file_name", "") or ""
                if url_items:
                    # A non-numeric key suffix becomes the display label
                    # (e.g. url_orcid -> "orcid: <url>"); bare "url" and
                    # numbered keys (url_1, url_2) stay plain.
                    display_values = []
                    for k, v in url_items:
                        label = k[len("url") :].lstrip("_")
                        if label and not label.isdigit():
                            display_values.append(f"{label}: {v}")
                        else:
                            display_values.append(v)
                    key: Hashable = tuple(sorted(v for _, v in url_items))
                elif file_name:
                    key = file_name
                    display_values = [file_name]
                else:
                    self.logger.warning(f"No metadata to show for {doc}")
                    continue
                if key not in seen or score > seen[key][1]:
                    seen[key] = (doc, score, display_values)
            ref_lines = [
                f"1.  {_format_scores(doc, score)}\n"
                + "\n".join(f"    - {v}" for v in values)
                for doc, score, values in sorted(
                    seen.values(), key=lambda x: x[1], reverse=True
                )
            ]
            md_lines += "\n".join(ref_lines)
            md_lines += "\n\n"

        display_md = "".join(md_lines) if md_lines else "No documents retrieved"
        status_data = NodeStreamData(
            heading="Reference Material",
            summary=f"{total_docs} docs from {len(per_domain_counts)} domains",
            display=display_md,
        )
        status_event = NodeStreamEvent(type="state", node=self.label, data=status_data)
        self.logger.debug(f"{status_event.model_dump() = }")
        self.write_custom_stream(status_event.model_dump())

        return {
            "reference_material": reference_material,
            "retrieval_attempts": retrieval_attempts,
        }
