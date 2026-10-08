#!/usr/bin/env python3
"""
Sensitive-file matcher for the client-side permission gate (ADR-0007).

The path-boundary check (``path_detect``) only asks about paths *outside* the
permitted roots.  This module is the second round: a curated, best-effort
matcher for files that commonly hold credentials (env files, private keys,
cloud config) so the gate can ask before a tool reads one that is *inside* the
project root.

It is advisory: a renamed secret, a secret inline in source, or a directory
scan (``grep -r``) can slip past a filename matcher.  Operators can extend the
list with the comma-separated ``KLEA_SENSITIVE_PATTERNS`` env var (extra
``fnmatch`` globs on the basename).

See ``devdocs/system/mcp-permissions.md``.

File: klea_utils/mcp/sensitive.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from fnmatch import fnmatch
from pathlib import Path

logger = logging.getLogger(__name__)

#: Env var with extra comma-separated basename globs to treat as sensitive.
SENSITIVE_ENV_VAR = "KLEA_SENSITIVE_PATTERNS"

#: Exact basenames (lowercased) that commonly hold credentials.
_SENSITIVE_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".netrc",
        "_netrc",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".htpasswd",
        ".pgpass",
        ".my.cnf",
        ".envrc",
        ".dockercfg",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        "kubeconfig",
        "secrets.yaml",
        "secrets.yml",
        "secrets.json",
        "secrets.toml",
    }
)

#: File suffixes (lowercased) that commonly hold keys/certs.
_SENSITIVE_SUFFIXES: frozenset[str] = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk", ".tfvars"}
)

#: Directory names (lowercased) anywhere in the path that hold credentials.
_SENSITIVE_DIRS: frozenset[str] = frozenset({".ssh", ".aws", ".gnupg", ".kube"})

#: Basename globs for families of sensitive files.
_SENSITIVE_GLOBS: tuple[str, ...] = (
    ".env.*",
    "*.env",
    "secrets.*",
    "service-account*.json",
    "*credentials.json",
)


def _extra_patterns() -> tuple[str, ...]:
    """Return the extra globs from :data:`SENSITIVE_ENV_VAR` (lowercased)."""
    raw = os.environ.get(SENSITIVE_ENV_VAR, "")
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def is_sensitive(path: str | os.PathLike) -> bool:
    """Return whether *path* is a well-known credential-bearing file.

    Matches on the basename (exact names and globs), the suffix, and any
    directory in the path (:data:`_SENSITIVE_DIRS`), plus the operator's extra
    patterns from :data:`SENSITIVE_ENV_VAR`.

    :param path: Path to test (need not exist).
    :returns: ``True`` when the path looks sensitive.
    """
    the_path = Path(path)
    name = the_path.name.lower()
    if name in _SENSITIVE_NAMES or the_path.suffix.lower() in _SENSITIVE_SUFFIXES:
        return True
    if any(part.lower() in _SENSITIVE_DIRS for part in the_path.parts):
        return True
    return any(
        fnmatch(name, pattern) for pattern in (*_SENSITIVE_GLOBS, *_extra_patterns())
    )
