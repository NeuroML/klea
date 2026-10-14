---
status: "accepted"
date: 2026-10-14
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Cross-frontend local identity

## Context and Problem Statement

Two frontends identify the same user differently.  The Textual TUI resolves
`user_id` through the `resolve_user_id()` seam (`--user-id` > `KLEA_USER_ID` >
a persisted local UUID, only for a loopback server; ADR-0050 I5).  The NiceGUI
web UI derives `user_id` from a per-browser NiceGUI session cookie
(`app.storage.user`).

Two problems follow.  (1) The web cookie is per browser profile/session, so a
new window, browser, cleared cookie, or different host (`localhost` vs
`127.0.0.1`) mints a new `user_id` and the user's chats appear to vanish.  (2)
The TUI and web cannot see each other's sessions because their `user_id`s
differ.  ADR-0050 deferred sharing an identity with the web.

The server has no auth and treats `user_id` as an opaque, client-supplied
token (`sessions.py`), so identity must stay client-local and must never be
inferred server-side.

## Decision Drivers

* The same sessions should be visible in the TUI and the web.
* Identity should be stable across windows/browsers for a local single-user
  setup.
* A machine-local identity must never be sent to a remote server, nor handed
  to a remote browser (it would expose the local user's chats).
* No server-side inference of identity (no auth yet).
* Agent and RAG are separate applications with separate graphs and session
  stores; their identities should not collide.

## Considered Options

* **A. Single host-local identity file, resolved through the shared seam.**
  Both frontends read `PlatformDirs(<server-app>).user_data_dir / "user_id"`;
  the web uses it only for loopback, else keeps the per-browser cookie.
* **B. Keep per-frontend identities.**  No sharing; each frontend keeps its
  own.
* **C. Server-side identity.**  Infer/mint identity on the server.

## Decision Outcome

Chosen option: **A**, because it makes sessions shared and identity stable for
the local single-user case without a server-side identity or leaking a local
identity remotely.

### Semantics

* The shared seam lives in `klea_utils/ui/identity.py` (extracted from the TUI;
  ADR-0050 I5): `resolve_user_id(explicit, env, server_url, app_name)` =
  explicit `--user-id` > `KLEA_USER_ID` > persisted local UUID, the last only
  when `server_url` is loopback.
* The persisted file is `PlatformDirs(identity_app_name).user_data_dir /
  "user_id"`, where `identity_app_name` is the **server** app (`klea-agent` /
  `klea-rag`), kept separate from the frontend's log `app_name` (an
  `app_name` is also the log filename, so the frontends cannot share it).
  Agent and RAG therefore have separate identity files.
* Both `klea tui` and `klea web` take `--user-id`; both resolve through the
  seam.  The web additionally guards the file: it is used only when **both**
  the backend URL and the requesting browser are loopback, so a `0.0.0.0`-bound
  web server never hands the local identity to a remote browser.  Otherwise the
  web falls back to the per-browser cookie (unchanged; multi-user).
* No first-run migration/adoption: `klea-agent` is unreleased, so there is no
  existing web identity to adopt.  An existing identity can be pinned by
  writing the id to the local file.
* Deferred: a remote/multi-user deployment still uses the per-browser cookie,
  so chats can still "disappear" on a new window until authentication provides
  a stable per-user id (see backlog).

### Consequences

* Good, because a local session opened in the TUI also appears in the web (and
  vice versa), and survives new windows/browsers.
* Good, because identity stays client-local; the file is loopback-gated and
  never sent remotely.
* Good, because agent and RAG keep separate identities and log names.
* Bad, because remote/multi-user still relies on the browser cookie, which is
  per browser profile/session (documented as a known limitation until auth).

### Confirmation

* `utils_pkg/tests/ui/test_identity.py` (shared resolution),
  `utils_pkg/tests/ui/web/test_web_identity.py` (web order: explicit/env >
  loopback file > cookie; remote client/server fall back to the cookie),
  `utils_pkg/tests/test_client_cli.py` (`--user-id` and `identity_app_name`
  routing for both `tui` and `web`).

## More Information

* ADR-0050 (Textual TUI; invariant I5, which deferred web/TUI identity
  sharing), ADR-0031 (per-app frontends over shared `klea_utils` components).
* `devdocs/system/tui.md`, `devdocs/system/web-ui-testing.md`.
