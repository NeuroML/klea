# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""klea_eval: the Klea evaluation harness.

This package implements the evaluation harness for the Klea project, the
machinery that runs, scores and reports on the evaluations described in ADRs
0056-0060. The command line interface lives in :mod:`klea_eval.ui.cli`.
"""

import logging

__version__ = "0.1.0"

logger = logging.getLogger(__name__)
