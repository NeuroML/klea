#!/usr/bin/env bash

# Manual end-to-end runner for the Klea agent.
#
# Copyright 2026 Ankur Sinha
# Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
#
# Usage:
#   e2e/run.sh                      # run all smoke scenarios
#   e2e/run.sh --collect-only -q    # list scenarios
#   e2e/run.sh -k create_file       # one scenario (pytest -k)
#   e2e/run.sh -m e2e_tools         # a feature group (pytest -m)
#   e2e/run.sh -k "not missing_file"  # deselect
#   e2e/run.sh -x -v                # stop on first failure, verbose
#
# Any arguments are forwarded to pytest.  Models and the API key are set here
# so they are easy to tweak; an already-exported value wins.

set -euo pipefail

cd "$(dirname "$0")/.."   # agent_pkg

export KLEA_AGENT_PLAN_MODEL="${KLEA_AGENT_PLAN_MODEL:-opencode-go:mimo-v2.5}"
export KLEA_AGENT_CHAT_MODEL="${KLEA_AGENT_CHAT_MODEL:-opencode-go:mimo-v2.5}"
export KLEA_E2E_WORKDIR="${KLEA_E2E_WORKDIR:-/tmp/opencode/klea-e2e}"
# Global wall-clock cap per CLI run (seconds); overrides each task's timeout.
export KLEA_E2E_TIMEOUT="${KLEA_E2E_TIMEOUT:-600}"

if [ -z "${OPENAI_API_KEY:-}" ] && command -v pass >/dev/null 2>&1; then
    OPENAI_API_KEY="$(pass API-keys/opencode.ai | head -1)"
    export OPENAI_API_KEY
fi

# -s shows the CLI transcript and the per-scenario report live.
pytest e2e/ -s "$@"
