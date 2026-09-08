---
status: "superseded by ADR-0033 (transport seam only)"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Runtime per-request model switching with user-supplied API keys

*Status: the *transport* decision in this ADR (the ``model_overrides_ctx``
contextvar seam, ADR-0033) is superseded.  The remaining decisions --
the ``configurable_fields="any"`` model, sessions-db per-chat override
sourcing, the three-layer merge, and key masking -- remain governing.
This file is retained for that record.*

## Context and Problem Statement

When Klea started, the LLM models for each role (``chat``,
``guard``, ``embedding``, ``plan``) were bound once at startup from
the env file (``KLEA_*_*_MODEL``) and the JSON config.  ``setup_llm``
created a concrete provider instance per role, so changing a model
meant editing the env file and restarting ``klea-rag-serve`` /
``klea-serve``.

This had two limitations:

* Users had to restart the apps to change models -- no per-chat or
  per-user choice without a restart.
* For public deployments (HuggingFace Spaces and any future
  institutional production), the deployer paid LLM usage fees: users
  could not supply their own ``HF_TOKEN`` / ``OPENAI_API_KEY`` /
  custom-endpoint keys per request, so every query was billed to the
  deployer's key.

How should Klea allow models and keys to change at runtime, per
request and per user, without restarting?

## Decision Drivers

* No restart to change models: a user should pick a chat/guard model
  from the web UI gear icon and have the next query use it.
* Deployer does not pay when users bring their own keys: public demos
  must let a browser supply its own ``api_key`` / ``HF_TOKEN`` per
  session/chat, so the deployer's key is only the fallback.
* Must compose with the existing ``BaseLangGraph`` env-schema generation
  (``{role}_model`` env vars) and the ``--profile`` / ``platformdirs``
  config layer.
* Must keep the graph topology unchanged for local single-user (env-file
  defaults remain the single source when no override is supplied).
* Must not leak API keys to logs (``klea_utils.plogging.mask_sensitive``).

## Considered Options

* **A. Static models only (old)** -- ``setup_llm(model_name)`` binds a
  concrete instance per role at ``_setup_models`` time.  Rejected: ties
  ``model_name`` to construction, so ``model_overrides`` per request is
  impossible; stale config leakage across chats.
* **B. Env-var only override** -- keep ``setup_llm`` but reload the env
  file per request.  Rejected: per-chat/per-user isolation would need
  one env file per chat and filesystem polling; still no per-request
  ``api_key`` without global env churn.
* **C. Configurable per-request model via LangChain ``configurable_fields="any"``
  (chosen)** -- ``klea_utils.llm.create_configurable_model`` creates a
  single ``_ConfigurableModel`` (``init_chat_model(..., configurable_fields="any")``)
  that carries no default model.  Each role's ``LLMModel`` wraps that
  instance; ``model_name`` is populated from the env via
  ``BaseLangGraph._apply_model_names`` as the *initial* value, but
  every ``graph.ainvoke(..., config={"configurable": overrides})`` may
  supply ``{model, api_key, base_url, provider_defaults, ...}`` that
  override the defaults for that invocation only.  A ``contextvar``
  (``graph/base.py:40`` ``model_overrides_ctx``) carries per-session
  overrides (``api_key, model, provider``) set by the API layer
  (``klea_utils.api.models`` / NiceGUI runner) into ``_invoke_llm``
  without threading overrides through node signatures.  ``modifiable``
  on ``LLMModel`` gates whether a role may be overridden per request
  (``guard`` is ``modifiable=False``).  The three-layer merge
  (role defaults -> context overrides -> node defaults) is explicit in
  ``llm.py:436``.

## Decision Outcome

Chosen option: "C. Configurable per-request model via ``configurable_fields='any'``".

* ``utils_pkg/klea_utils/llm.py:808`` ``create_configurable_model(logger)``
  -> ``_ConfigurableModel`` with ``configurable_fields="any"``; dynamic
  provider field introspection so ``api_key`` / ``base_url`` pass
  through for ``huggingface``, ``openai``, ``custom``.  Generic
  ``api_key`` is mapped to ``huggingfacehub_api_token`` / provider
  fields per ``llm.py:1036``.
* ``BaseLangGraph``: ``_setup_models`` populates ``llm_models`` with
  ``LLMModel(instance=create_configurable_model(...), required=...)``;
  ``_apply_model_names`` fills ``model_name`` from the generated
  ``{role}_model`` env fields; ``resolve_langchain_endpoint`` handles
  custom ``base_url`` probing for ``get_catalog_model_limits``.
  ``model_overrides_ctx`` (``contextvar`` ``dict``) is the per-request
  seam.
* API: ``klea_utils/api/models.py`` per-session ``/models`` endpoints
  (``api_key`` masked in logs via ``plogging.mask_sensitive``) and the
  NiceGUI gear-icon model picker per chat
  (``klea_utils.ui.web.nicegui.components.model_dialog``);
  ``klea_utils/nodes/base.py:475`` reads ``overrides.get("api_key")``
  in ``_invoke_llm``.
* Embedding role: ``LLMModel(instance=None, required=...)`` carries
  only the embedding model name (no chat instance); ``_has_vector_stores``
  gates ``required`` after ``_configure_resources``.  Guard role has
  ``required=False, modifiable=False`` so it warns but does not block
  startup and cannot be changed per chat.

### Consequences

* Good, because no restart to change models: web UI or ``POST /models``
  per session updates ``model_overrides_ctx`` and the next
  ``ainvoke`` uses the new model; shell env vars remain the default
  when no override is present.
* Good, because public deployments can let users supply their own
  ``api_key`` / ``HF_TOKEN`` per browser session -- the deployer's key
  is the fallback, not the payer.  Resolves the grant cost limitation.
* Good, because the graph topology is unchanged (single
  ``_ConfigurableModel`` per role); node prompts and ``BaseLLMNode``
  ``model_defaults`` still apply via the three-layer merge.
* Bad, because a configurable model adds indirection: dynamic provider
  introspection and ``_ConfigurableModel._model(config)`` internals are
  harder to reason about than a concrete ``ChatOllama`` instance; stale
  overrides in the ``contextvar`` must be cleared per request.
* Bad, because model switching is still chat-scoped via the API/NiceGUI
  seam; CLI single-query mode (``klea-rag cli --single-query``) still
  uses env-file defaults unless an explicit ``--server`` override is
  threaded.

### Confirmation

* ``utils_pkg/tests/test_llm_model.py:107`` ``test_api_key_from_override``
  and ``api_key -> huggingfacehub_api_token`` mapping tests;
  ``graph/base.py:40`` ``model_overrides_ctx`` propagation tests.
* ``docs/install.rst:221`` Choosing models + ``docs/concepts/mcp.rst``
  model-type notes still describe the ``{role}_model`` env vars; web UI
  gear icon and ``/models`` docs reference the same ``configurable``
  mechanism.
* ``ty`` extra-paths still resolve ``BaseLangGraph`` + ``LLMModel``;
  ``ruff`` clean for ``llm.py``/``graph/base.py``/``api/models.py``.
* Live: ``KLEA_RAG_CHAT_MODEL=ollama:qwen3:0.6b klea-rag-serve`` with web
  UI gear-icon switch to ``huggingface:org/model:auto`` + pasted
  ``HF_TOKEN`` per chat succeeds without restart; deployer's env key
  remains the fallback.

## Pros and Cons of the Options

### Configurable per-request model (chosen)

* Good, because per-request/per-chat model and key override without restart
* Good, because deployer not billed when users BYO keys
* Good, because graph topology unchanged (single ``_ConfigurableModel``)
* Bad, because dynamic provider introspection and ``contextvar`` add complexity

### Static models only

* Good, because simple concrete instances
* Bad, because restart required to change models; deployer pays all fees

### Env-var only override

* Good, because no new API seam
* Bad, because no per-chat isolation; global env churn

## More Information

* Code: ``utils_pkg/klea_utils/llm.py:808`` (``create_configurable_model``),
  ``graph/base.py:40`` (``model_overrides_ctx``), ``graph/base.py:116``
  (``_setup_models``/``_apply_model_names``), ``nodes/base.py:475``
  (``overrides.get("api_key")``), ``api/models.py`` (per-session
  endpoints), ``ui/web/nicegui/runner.py:322`` (gear icon ``api_key``
  handling), ``plogging.py:211`` (``mask_sensitive``), ``models_catalog.py``
  (``_langchain_provider_api_key_env``).
* Related: ``.agents/2026-07-29.md`` (model config refactor to
  configurable fields), ``devdocs/README.md:27`` store-create note
  (remains valid); ``docs/install.rst:198`` model env vars still the
  default layer, now overridden per request.
* Decisions codified ``2026-08-28``; refactor landed ``2026-07-29``
  ``feat(llm): move LLMModel to llm module`` + ``2026-07-21/28``
  ``utils: use configurable model instance`` (``95fc002``/``66608b7``).
