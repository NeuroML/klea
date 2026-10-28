# Utils release checklist

Status: procedure for cutting a `klea_utils` release (`utils_pkg`).
`klea_rag` has its own list (`rag-release-checklist.md`). `klea_agent`
and `neuroml_mcp` stay unreleased (see `CHANGELOG.md`).

`klea_utils` is the shared library for the other packages, so this
checklist puts more weight on API / ABI care than the RAG list. The
project is still on 0.x and does not promise API compatibility yet, but
releases follow semantic versioning and breaking changes must be called
out. Compatibility will get stricter once the 1.x line starts.

The workflow builds `utils_pkg` from a `klea_utils-v*` tag with
`uv build`. A manual run (`workflow_dispatch`, package `klea_utils`)
uploads to TestPyPI. A tag push uploads to PyPI. Releases are cut with
tags only; this repo does not use GitHub Releases for publishing. The
tag version is not compared with `version` in `setup.cfg`, so those two
have to be checked by hand.

## Before the tag

- [ ] Check the last utils tag
      (`git tag -l 'klea_utils-v*' --sort=-v:refname`). List what
      changed in `utils_pkg/` (and shared docs/changelog) since that
      tag.
- [ ] Classify the bump under semver on 0.x:
      - breaking public API or on-disk / wire format change -> call it
        out under `### Breaking changes` in `CHANGELOG.md`, and bump
        the version so consumers notice (today that is still a 0.x
        bump; 1.x will treat breaking changes as a major).
      - additive API only -> minor-style 0.x bump.
      - fix only -> patch-style 0.x bump.
- [ ] API / ABI review against consumers. Walk the diff for removed or
      renamed public names in `klea_utils` that `klea_rag` or
      `klea_agent` import (graph, nodes, MCP tools, stores, UI helpers,
      config). A `klea-agent` pass over the git log can help. If a
      consumer must change, note it in the changelog and plan that
      package's follow-up release (RAG: `rag-release-checklist.md`).
- [ ] Bump `version` in `utils_pkg/setup.cfg`. The tag pushed later has
      to be that same number (`klea_utils-vX.Y.Z` for
      `version = X.Y.Z`).
- [ ] Check `CHANGELOG.md` is up to date for this utils release. The
      unreleased `klea_utils` / `klea_rag` heading may still be shared;
      only ship the utils tag from this checklist.
- [ ] CI is green on the commit that will be tagged. If that commit
      changes `docs/` or a package, the docs workflow is green too
      (`sphinx-build -W docs docs/_build` in
      `.github/workflows/docs.yml`).

## TestPyPI

Run this from the commit whose `setup.cfg` version you are testing.
Actions -> "Upload Python Package" -> Run workflow -> `klea_utils`.
That run uploads to TestPyPI (see the comment on `workflow_dispatch` in
`.github/workflows/python-publish.yml`). TestPyPI rejects a file
version that is already there, so the dry-run version has to be one
TestPyPI does not have yet.

- [ ] In a fresh virtualenv, install the TestPyPI build and take every
      other dependency from PyPI. Either:

      ```
      pip install \
        --index-url https://test.pypi.org/simple/ \
        --extra-index-url https://pypi.org/simple/ \
        "klea-utils[mcp,ingest,chroma,nicegui,tui]"
      ```

      or with uv:

      ```
      uv pip install \
        --index-url https://test.pypi.org/simple/ \
        --extra-index-url https://pypi.org/simple/ \
        "klea-utils[mcp,ingest,chroma,nicegui,tui]"
      ```

- [ ] `pip show klea-utils` reports the version just uploaded.
- [ ] `klea-stores-create --help` and `klea-mcp --help` run.
- [ ] Consumer smoke: in the same or a second fresh env, install
      `klea-rag` (and optionally a local `klea-agent` checkout) against
      this TestPyPI `klea-utils`. Imports and a short RAG server start
      (`klea-rag-serve --help` / health check) must still work. If a
      consumer breaks, stop and fix or document the break before the
      real tag.

## Manual checks

These are done by hand on the TestPyPI install.

- [ ] Utils-facing docs match this release: `docs/components/utils.rst`,
      `docs/api/` for `klea_utils`, and the CLI pages for
      `klea-stores-create` / `klea-mcp`. A green docs build only means
      the pages compile.
- [ ] In the fresh virtualenv:

      ```
      pip-audit
      ```

      Nothing it reports blocks the release.
- [ ] Code security audit of this release. How to run it is still open
      (`klea-agent`, or a dedicated AI / non-AI tool). Until that is
      decided, note what was tried in the release notes.
- [ ] On-disk formats used by utils still load: an existing Chroma /
      BM25 store from the previous release opens with the new build
      (same embedding model). A store created by the new
      `klea-stores-create` is readable by the previous released RAG if
      this bump is not marked breaking for store format.

## Automated E2E tests and evaluation

TODO. Once the evaluation / E2E harness exists
(`system/agent-evaluation-harness.md`), add the commands that replace
the consumer smoke above. An AI-agent security review can sit here too
when that path is chosen.

## Tag

Tag the same commit that was smoked. Push the tag to `NeuroML/klea`.
This checklist publishes `klea_utils` only. Do not create a GitHub
Release for the tag: this monorepo publishes from tags only, and a
published Release would run the workflow a second time.

```
git tag klea_utils-vX.Y.Z
git push git@github.com:NeuroML/klea.git klea_utils-vX.Y.Z
```

After the utils tag is on PyPI, use `rag-release-checklist.md` for a
paired `klea_rag` release when RAG should ship too, and set a minimum
`klea_utils` version in `rag_pkg/setup.cfg` if one is not set yet.
