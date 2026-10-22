# RAG release checklist

Status: procedure for cutting a `klea_utils` and `klea_rag` release.
`klea_agent` and `neuroml_mcp` stay unreleased (see `CHANGELOG.md`).

Scope of this note: the procedure only.
`.github/workflows/python-publish.yml` stays as it is.

The workflow builds one package folder from the name in the tag
(`klea_utils` -> `utils_pkg`, `klea_rag` -> `rag_pkg`) with `uv build`.
A manual run (`workflow_dispatch`) uploads that build to TestPyPI.
A tag push uploads it to PyPI. The tag version is not compared with
`version` in `setup.cfg`, so those two have to be checked by hand.

Last released tags: `klea_utils-v0.5.0`, `klea_rag-v0.5.0`.
`utils_pkg/setup.cfg` and `rag_pkg/setup.cfg` currently say `0.6.0`.

## Before the tag

- [ ] If `klea_utils` changed since `klea_utils-v0.5.0`, release it
  ```
  first. Then set a minimum on the RAG dependency. Today
  `rag_pkg/setup.cfg` has `klea_utils[rerank]` with no version;
  after a utils `X.Y.Z` tag that line becomes
  `klea_utils[rerank]>=X.Y.Z`.
  ```
- [ ] Bump `version` in `utils_pkg/setup.cfg` and `rag_pkg/setup.cfg`
  ```
  for each package that is shipping. The tag pushed later has to
  be that same number (`klea_rag-v0.6.0` for `version = 0.6.0`).
  ```
- [ ] Update `CHANGELOG.md`. Tagged releases share one heading for
  ```
  `klea_utils` / `klea_rag`.
  ```
- [ ] CI is green on the commit that will be tagged. If that commit
  ```
  changes `docs/` or a package, the docs workflow is green too
  (`sphinx-build -W docs docs/_build` in
  `.github/workflows/docs.yml`). That workflow runs on push to
  `development` and `main`.
  ```

## TestPyPI

Run this from the commit whose `setup.cfg` version you are testing.
Actions -> "Upload Python Package" -> Run workflow. Pick `klea_utils`
first when utils is part of this release, then `klea_rag`. That run
uploads to TestPyPI. TestPyPI rejects a file version that is already
there, so the dry-run version has to be one TestPyPI does not have yet.

- [ ] In a fresh virtualenv, install the TestPyPI build and take every
  ```
  other dependency from PyPI:

  ```
  pip install \
    --index-url https://test.pypi.org/simple/ \
    --extra-index-url https://pypi.org/simple/ \
    "klea-rag[chroma,nicegui,tui]"
  ```
  ```

- [ ] `pip show klea-utils` and `pip show klea-rag` report the versions
  ```
  just uploaded. When both indexes have a copy, this is the check
  that the fresh env did not keep an older `klea-utils` from PyPI.
  ```
- [ ] `klea-rag --help` and `klea-rag-serve --help` run. When utils
  ```
  shipped in this release, `klea-stores-create --help` and
  `klea-mcp --help` run too.
  ```
- [ ] Start the server against a store built by the previous release.
  ```
  `--profile` is a file name, not a path: `klea_rag` loads
  `klea_rag.json` from the working directory. Store paths inside
  that file are also relative to the working directory
  (`chroma:../../deployments/huggingface/store/nml-elife` from
  `rag_pkg/example-configs`). That store is local data; it is not
  in the git checkout. Use it when it is already on disk, and use
  the same embedding model the store was built with. Otherwise
  point a profile at any Chroma folder from the last release.

  ```
  cd rag_pkg/example-configs
  klea-rag-serve --profile klea_rag
  ```

  In a second terminal:

  ```
  curl http://127.0.0.1:8005/health/ready
  ```

  `curl` returns 200. The server loads stores during startup, before
  it reports ready, so a missing store fails this check.
  ```
- [ ] Send one real query. This needs the models and keys from the
  ```
  profile. A 200 with a `result` is the pass.

  ```
  curl -s http://127.0.0.1:8005/query \
    -H 'content-type: application/json' \
    -d '{"query":"What is NeuroML?","chat_id":"release-smoke","user_id":"release"}'
  ```
  ```

- [ ] `klea-rag web` opens (the `nicegui` extra). `klea-rag`, or
  ```
  `klea-rag tui`, opens (the `tui` extra). Bare `klea-rag` is the
  Textual TUI. The old `klea-rag cli` subcommand is gone.
  ```
- [ ] The evaluation harness is added to this list once it exists
  ```
  (`system/agent-evaluation-harness.md`). Until then the smoke
  above is the check.
  ```

## Tag

Tag the same commit that was smoked. Push the tag to `NeuroML/klea`.
Push the utils tag first when utils is in this release, then the RAG
tag. Each tag publishes only that package.

```
git tag klea_utils-vX.Y.Z
git push upstream klea_utils-vX.Y.Z
git tag klea_rag-vX.Y.Z
git push upstream klea_rag-vX.Y.Z
```

Stop after the tag push. Publishing a GitHub Release for the same tag
runs the workflow again (`release: published`), and the second upload
to PyPI fails.