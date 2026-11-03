# Agent release checklist

Status: procedure for cutting a `klea_agent` release (`agent_pkg`).
`klea_utils` and `klea_rag` have their own lists
(`utils-release-checklist.md`, `rag-release-checklist.md`).
`neuroml_mcp` stays unreleased (see `CHANGELOG.md`).

`klea_agent` has not shipped to PyPI yet (`agent_pkg/setup.cfg` is still
on the pre-release line, and there is no `klea_agent-v*` tag). This
checklist is for the first release and the ones after it. The agent
depends on `klea_utils[mcp]` with no minimum today, so a utils release
must come first when utils changed
(`utils-release-checklist.md`).

The workflow builds `agent_pkg` from a `klea_agent-v*` tag with
`uv build`. A manual run (`workflow_dispatch`, package `klea_agent`)
uploads to TestPyPI. A tag push uploads to PyPI. Releases are cut with
tags only; this repo does not use GitHub Releases for publishing. The
tag version is not compared with `version` in `setup.cfg`, so those two
have to be checked by hand.

## Before the tag

- [ ] Check the last tags
      (`git tag -l 'klea_utils-v*' 'klea_agent-v*' --sort=-v:refname`).
      If `klea_utils` changed since its last tag, release it first
      (`utils-release-checklist.md`). Then set a minimum on the agent
      dependency. Today `agent_pkg/setup.cfg` has `klea_utils[mcp]`
      with no version; after a utils `X.Y.Z` tag that line becomes
      `klea_utils[mcp]>=X.Y.Z`.
- [ ] Bump `version` in `agent_pkg/setup.cfg`. The tag pushed later has
      to be that same number (`klea_agent-vX.Y.Z` for
      `version = X.Y.Z`). The first release should leave the WIP /
      unreleased note in `CHANGELOG.md` once this package is on PyPI.
- [ ] Check `CHANGELOG.md` is up to date for this agent release. Agent
      entries live under the `klea_agent` unreleased heading (separate
      from the shared `klea_utils` / `klea_rag` heading). A
      `klea-agent` pass over the git log since the last agent tag (or
      since the agent work began, for the first release) can help catch
      missing entries.
- [ ] CI is green on the commit that will be tagged. If that commit
      changes `docs/` or a package, the docs workflow is green too
      (`sphinx-build -W docs docs/_build` in
      `.github/workflows/docs.yml`).

## TestPyPI

Run this from the commit whose `setup.cfg` version you are testing.
Actions -> "Upload Python Package" -> Run workflow -> `klea_agent`.
That run uploads to TestPyPI (see the comment on `workflow_dispatch` in
`.github/workflows/python-publish.yml`). TestPyPI rejects a file
version that is already there, so the dry-run version has to be one
TestPyPI does not have yet. When this release needs a new utils build
too, upload `klea_utils` to TestPyPI first.

- [ ] In a fresh virtualenv, install the TestPyPI build and take every
      other dependency from PyPI. Either:

      ```
      pip install \
        --index-url https://test.pypi.org/simple/ \
        --extra-index-url https://pypi.org/simple/ \
        "klea-agent[nicegui,tui]"
      ```

      or with uv:

      ```
      uv pip install \
        --index-url https://test.pypi.org/simple/ \
        --extra-index-url https://pypi.org/simple/ \
        "klea-agent[nicegui,tui]"
      ```

- [ ] `pip show klea-agent` and `pip show klea-utils` report the
      versions just uploaded (or the minimum utils version this agent
      requires). When both indexes have a copy, this is the check that
      the fresh env did not keep an older `klea-utils` from PyPI.
- [ ] `klea --help` and `klea-serve --help` run.
- [ ] Start the server. A JSON profile is optional: with no
      `--profile` the agent starts from built-in defaults plus the
      environment (`klea_agent.env` by default). Default port is
      `8006`.

      ```
      klea-serve
      ```

      In a second terminal:

      ```
      curl http://127.0.0.1:8006/health/ready
      ```

      `curl` returns 200.
- [ ] Send one real query. Models and keys come from the env file /
      credentials. A 200 with a `result` is the pass.

      ```
      curl -s http://127.0.0.1:8006/query \
        -H 'content-type: application/json' \
        -d '{"query":"Say hello in one short sentence.","chat_id":"release-smoke","user_id":"release"}'
      ```

- [ ] `klea web` opens (the `nicegui` extra). `klea`, or `klea tui`,
      opens (the `tui` extra). Bare `klea` is the Textual TUI. The old
      `klea cli` subcommand is gone.

## Manual checks

These are done by hand on the TestPyPI install.

- [ ] User-facing docs (`docs/guides/`, `docs/cli/`,
      `docs/components/agent.rst`, TUI / web pages) and the API docs
      match this release. A green docs build only means the pages
      compile.
- [ ] In the fresh virtualenv:

      ```
      pip-audit
      ```

      Nothing it reports blocks the release.
- [ ] Code security audit of this release. How to run it is still open
      (`klea-agent`, or a dedicated AI / non-AI tool). Until that is
      decided, note what was tried in the release notes.
- [ ] One query each with a HuggingFace, OpenCode, Anthropic, and
      OpenAI model.
- [ ] Web and TUI: the dialogs and buttons work, including model and
      credential setup, mode (general / scientific), and tool access
      level.
- [ ] Config and session data: after a restart, chats reload, and the
      inspection pane restores with them.
- [ ] Cancel a running query, and resume a failed one.
- [ ] Restart the web UI and the TUI. The same session is still there.
- [ ] Send stays blocked when a required model or API key is missing.
- [ ] One turn uses a tool, not only a plain chat reply (full access).
- [ ] HITL: a plan review or path-permission ask appears and can be
      answered or cancelled.
- [ ] Parallel clients against one `klea-serve`:
      - Start `klea-serve`.
      - In one venv, open `klea tui` and list the environment (for
        example the Python version).
      - In a second venv with a different Python, open another
        `klea tui` and list the environment.
      - Each TUI shows its own client environment, not the server's.
      - Open a web client against the same server: the web session
        shows the server's environment.

## Automated E2E tests and evaluation

TODO. Once the evaluation / E2E harness exists
(`system/agent-evaluation-harness.md`, ADRs 0056-0060), add the
commands that replace the manual smoke and UX checks above. An
AI-agent security review can sit here too when that path is chosen.

## Tag

Tag the same commit that was smoked. Push the tag to `NeuroML/klea`.
This checklist publishes `klea_agent` only. Do not create a GitHub
Release for the tag: this monorepo publishes from tags only, and a
published Release would run the workflow a second time.

```
git tag klea_agent-vX.Y.Z
git push git@github.com:NeuroML/klea.git klea_agent-vX.Y.Z
```

When utils also ships in the same cut, finish
`utils-release-checklist.md` first, then this tag.
