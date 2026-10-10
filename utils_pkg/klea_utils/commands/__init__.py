#!/usr/bin/env python3
"""
Shared session-command framework (ADR-0047).

The common command framework lives in :mod:`klea_utils.commands.common`;
frontend and graph adapters are added as separate modules in this sub-package.
Import from the specific module, not this package.

File: klea_utils/commands/__init__.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""
