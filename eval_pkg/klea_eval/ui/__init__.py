# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""User interface subpackage for klea_eval.

The user-facing entry points live here, currently the command line interface in
``klea_eval.ui.cli``.  The package holds no harness logic of its own: it only
wires user input to the loading, execution, scoring and storage modules.


Houses the command line entry point exposed to users through the
``klea-eval`` console script.
"""
