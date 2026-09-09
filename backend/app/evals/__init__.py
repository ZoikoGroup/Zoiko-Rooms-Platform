"""Deterministic evaluation harness (Phase 6).

See ``runner.run_evals`` for the release-gated report. Use the CLI
(``python -m app.evals`` or ``scripts/run_evals.py``) to emit a versioned JSON
report suitable for release gating.
"""

from app.evals.runner import EVAL_VERSION, report_json, run_evals

__all__ = ["EVAL_VERSION", "report_json", "run_evals"]
