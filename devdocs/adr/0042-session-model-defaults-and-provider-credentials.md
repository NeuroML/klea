---
status: "accepted"
date: 2026-09-28
decision-makers: Ankur Sinha
consulted: "opencode agent"
informed: ""
---

# Per-session model defaults and provider-scoped credentials

## Context and Problem Statement

Model selection was per-chat only (ADR-0014, ADR-0033): an override stored a
model string and, in the same record, a plaintext provider API key. The web UI
exposed the selector only from a chat's status pane, so a brand-new user with
no env-configured models had no way to configure one before the first message;
the first query then failed at the node layer ("No model configured").

Storing the key inside the per-chat model override is also the wrong shape: a
key is a credential for a provider (and, for custom/explicit endpoints, an
endpoint), not for a role or model. Re-using an override's key when only the
model changes is unsafe if the provider changed, and a per-chat key cannot be
inherited by other chats or the (future) TUI.

How should users configure models and credentials so the app works on first
run, remembers their choices, keeps the per-chat isolation feature, and does
not scatter provider secrets across model records?

## Decision Drivers

* First-run must work: no chat exists yet, so model/key selection cannot
  require a chat.
* Per-chat model isolation is a distinguishing feature and must remain.
* "Mimic env vars": the UI default should behave like a per-session default
  above env config.
* Credentials belong to providers/endpoints; a model string already encodes the
  provider (and any custom endpoint).
* Secrets must not be returned raw by the API and should not live at rest
  forever.
* Shared components: the same behaviour for web, Streamlit, and the TUI.

## Considered Options

* **A. Global (process-wide) model selection** -- one model set for the whole
  server.  *Rejected:* not safe on a multi-user deployment and loses per-chat
  overrides.
* **B. Create a chat eagerly / configure models per chat only** -- *Rejected:*
  reconfiguring every chat; no first-run path.
* **C. Per-session model defaults + provider-scoped credentials** (chosen).

## Decision Outcome

Chosen option: "C. Per-session model defaults + provider-scoped credentials".

Three-layer model resolution, lowest priority first:

1. graph/env defaults (`llm_models`),
2. per-session default overrides (keyed by the persistent browser `user_id`),
3. per-chat overrides.

Overrides store only the model string. Saving a model in a chat promotes the
changed role into the per-session default ("last used"), so new chats inherit
it; `Reset` clears only the chat override and falls back to the default. There
is no scope toggle: the dialog edits the session defaults when no chat is
active and the chat otherwise.

API credentials are stored separately, per user, keyed by provider (plus the
endpoint for `custom:` / explicit-URL models), and are resolved into the
per-run `model_overrides` by `chat_core` (skipping locked roles). The UI is
write-only and masked: the API never returns a stored secret, only a masked
suffix and a `source` of `user` / `env` / `none`; providers that need no key
(e.g. Ollama) are excluded from readiness checks.

Unused credentials are purged after a TTL (default 7 days,
`KLEA_CREDENTIAL_TTL_DAYS`; `0` disables), measured from `last_used_at` and
swept at startup and opportunistically on access. Secrets are stored in
plaintext, mirroring the opencode config behaviour.

Legacy per-chat inline `api_key`s (released in `klea_utils` 0.5.x) are migrated
into the credential store at startup and stripped from the override.

### Consequences

* Good: first run works; models are selectable before any chat; per-chat
  isolation is preserved; the default mirrors env-var behaviour.
* Good: keys are no longer duplicated across model records, are never returned
  raw, and expire when unused.
* Bad: a change in one chat updates the default for future chats (inherent to
  "last used"); there is no one-off per-chat model without later changing the
  default.
* Neutral: plaintext at rest (opencode parity); hosted deployments should rely
  on env vars or a future auth-provided key.
* Neutral: readiness now depends on credentials as well as models; send is
  disabled until both are configured.

### Confirmation

* `utils_pkg/tests` cover value/role precedence, credential injection, legacy
  migration, the TTL sweep, credential API masking, and the readiness helpers.
* `ty` and `ruff` gate the changed modules.

## More Information

* Supersedes the per-chat-only model override semantics of ADR-0014; the
  runtime-context transport of ADR-0033 is unchanged (it now carries the
  merged session+chat overrides plus injected credentials).
* Backlog: model/provider picker backed by the models.dev catalog;
  per-request/session-only credentials; OS keyring; auth-provided key
  encryption at rest.
