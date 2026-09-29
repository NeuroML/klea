---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Layered config via env file and profile-resolved JSON

## Context and Problem Statement

Klea RAG and the agent both need model choices and store wiring at
startup.  Early Klea loaded a single ``KLEA_*_APP_CONFIG_FILE`` JSON
pointed to *inside* an env file: the env file was mandatory, the JSON
name was indirected through it, and ``--profile`` did not exist.

This was easy to misconfigure (which env var wins?) and unfriendly for
single-user use: users want to switch domains by managing profile
files in one location (``~/.config/klea-rag/``) rather than editing env
vars -- the pattern most single-user apps follow.  HuggingFace Spaces
and containers also prefer a single explicit flag, and local users want
to switch domains without editing env files.

How should env-file and JSON resolution compose, and how should users
switch profiles without hand-edited ``KLEA_*_APP_CONFIG_FILE``?

## Decision Drivers

* One explicit switch for local iteration (``--profile <name>``) that
  works in the working directory without editing env files; more
  generally, single-user apps tend to keep profile files in one
  location (``~/.config/klea-rag/`` via ``platformdirs``) so users
  manage named profiles rather than editing env vars.
* Deployments must still override via env (``KLEA_RAG_ENV_FILE``,
  ``KLEA_*_APP_CONFIG_FILE``, ``XDG_CONFIG_HOME``) for container and
  HF Spaces use.
* JSON template scaffolding (``--profile template``) should be safe
  (refuse to overwrite an existing file) and visible in the CWD.
* ``BaseLangGraph`` env schema is generated from ``llm_models`` roles
  (``{role}_model``) so profile resolution must happen *after* model
  setup but *before* ``RetrieverConfig`` wiring.

## Considered Options

* **A. JSON path inside env file only (old)** -- ``KLEA_RAG_APP_CONFIG_FILE``
  (or the same key inside the env file) holds the JSON path.  Rejected:
  requires editing the env file to switch domains; `--profile`` cannot
  be inferred without opening the env file.
* **B. Env file optional + ``--profile`` CWD-first with
  ``platformdirs`` fallback (chosen)** -- ``KLEA_*_ENV_FILE``
  (default ``rag.env`` / ``klea_agent.env``) is optional; when absent
  the process ``environ`` and ``BaseSettings`` defaults are used.
  ``--profile <name>`` (default ``klea_rag`` / ``klea_agent``) resolves
  ``<name>.json`` by searching ``CWD`` first, then the per-app
  ``platformdirs`` config dir (``~/.config/klea-rag/``,
  honoring ``XDG_CONFIG_HOME``).  ``KLEA_*_APP_CONFIG_FILE`` as an env
  var (or as a key inside the env file) is still honoured when
  ``--profile`` is not given, and process-env beats env-file due to
  ``pydantic-settings`` precedence.  ``--profile template`` scaffolds
  a fresh ``klea_rag.json`` / ``klea_agent.json`` from
  ``AppConfig`` defaults in the CWD and refuses if it exists.
* **C. Single YAML/TOML** -- merge env and JSON into one file.
  Rejected: the ``provider:model_id`` / ``custom:model:base_url``
  model naming plus ``HF_TOKEN`` / ``OPENAI_API_KEY`` env secrets are
  naturally ``k=v`` env entries; coupling them to the JSON would leak
  secrets into the profile file.

## Decision Outcome

Chosen option: "B. Env file optional + ``--profile`` CWD-first with
``platformdirs`` fallback".

* ``utils_pkg/klea_utils/paths.py:53`` ``resolve_app_config_path(config_file,
  conf_dir, cwd=None)`` searches ``cwd`` first, then ``conf_dir``;
  absolute paths are returned if they exist and ``ValueError`` is
  raised on empty input.
* ``utils_pkg/klea_utils/graph/base.py:193`` ``_build_env_class`` (from
  ``llm_models`` roles) + ``_load_env``: missing env file falls back
  to ``process environ`` + class defaults; ``app_config_file`` is taken
  from the generated ``{role}_model`` env fields and the profile
  resolution in ``paths.py`` (plus ``write_config_template`` for
  ``template``).
* ``utils_pkg/klea_utils/ui/cli.py`` / ``api/server.py`` thread
  ``--profile`` through to ``BaseLangGraph._apply_model_names`` and
  ``resolve_app_config_path`` via ``klea_utils/paths.get_config_dir``.
* ``agent_pkg/klea_agent/config.py`` / ``rag_pkg/klea_rag/config.py``
  no longer require ``KLEA_*_APP_CONFIG_FILE`` inside the env file;
  ``AppConfig.general`` defaults remain the JSON authority.
* ``AGENTS.md`` Config & env loading documents the precedence
  (``KLEA_*_ENV_FILE`` -> profile CWD/`platformdirs`/`XDG_CONFIG_HOME` ->
  ``KLEA_*_APP_CONFIG_FILE`` fallback -> ``--profile template``).

### Consequences

* Good, because local iteration and single-user use are one flag or
  one config dir: ``--profile my-study`` picks up ``my-study.json``
  in the working directory or ``~/.config/klea-rag/my-study.json``
  without touching env vars; named profiles in the config dir are the
  normal single-user-app pattern.  ``--profile template`` is
  discoverable via ``--help``.
* Good, because container/HF deployments still override via
  ``KLEA_*_ENV_FILE`` / ``KLEA_*_APP_CONFIG_FILE`` / ``XDG_CONFIG_HOME``
  without code changes.
* Good, because the env file is optional: a clean machine can run from
  ``process environ`` + a single ``--profile`` JSON.
* Bad, because env-file-absent behaviour must be remembered by tools
  that read ``KLEA_*_APP_CONFIG_FILE`` from the env file (now optional);
  the fallback through ``paths.py`` is the new source of truth.
* Bad, because ``platformdirs`` config dir is OS-specific
  (``~/.config/klea-rag/`` on Linux vs ``~/Library/Preferences`` on
  macOS); Windows contributors see a different path.

### Confirmation

* ``utils_pkg/klea_utils/tests/test_config_resolution.py``-style
  coverage (renamed via ``paths.py`` unit tests) asserts
  ``resolve_app_config_path`` CWD-first, absolute, and
  ``FileNotFoundError`` paths.
* ``AGENTS.md`` Config & env loading paragraph matches
  ``graph/base.py:193`` behaviour; ``ty.toml`` ``extra-paths`` still
  resolve ``BaseLangGraph`` cross-package.
* Manual: ``klea-rag-serve --profile my-study`` in a dir with
  ``my-study.json`` loads the CWD file; absent file falls back to
  ``~/.config/klea-rag/my-study.json`` when ``XDG_CONFIG_HOME`` is set.

## Pros and Cons of the Options

### Env file optional + --profile CWD-first with platformdirs (chosen)

* Good, because one explicit switch for local iteration
* Good, because container/HF still overrides via env
* Good, because ``template`` scaffolds discoverably
* Bad, because env-file no longer the single source of truth

### JSON path inside env file only

* Good, because single indirection
* Bad, because switching domains requires editing the env file

## More Information

* Code: ``utils_pkg/klea_utils/paths.py:53`` (``resolve_app_config_path``),
  ``graph/base.py:193`` (``_build_env_class`` / ``_load_env``),
  ``ui/cli.py`` / ``api/server.py`` (``--profile`` threading),
  ``klea_agent/config.py`` / ``klea_rag/config.py`` (``AppConfig``),
  ``AGENTS.md`` Config & env loading.
* Related: ``devdocs/adr/0006-monorepo.md`` (monorepo layout that makes
  the ``platformdirs`` dir per-package isolated), ``docs/install.rst:161``
  env + profile description (remains the user-facing truth).
* Commits: ``b31b8d3`` (profiles: add config path resolver),
  ``dc0cc27`` (thread options through CLI), ``3164398`` (make env files
  optional), ``b3ae10f`` (template creation).
* Codified ``2026-08-28``; profile path resolver landed ``2026-08-17``.

## Addendum (2026-09-29)

The Agent no longer declares a default config file
(``KleaAgent.config_file_default = ""``): with no ``--profile`` (and no
``KLEA_AGENT_APP_CONFIG_FILE``) it runs from ``AppConfig`` defaults plus the
environment, so a JSON config is optional.  This supersedes the
"default profile is ``klea_agent``" statement for the Agent only.  The RAG
keeps ``config_file_default = "klea_rag.json"`` and still requires its JSON
config (its store wiring has no sensible default).
``BaseLangGraph._load_env`` skips JSON loading when no file is selected and
the default is empty.
