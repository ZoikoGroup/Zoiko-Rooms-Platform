"""Phase 6 evaluation-suite integration test (ZR-AI-EVAL-001).

Runs the deterministic evaluation harness as part of the normal suite so
release-gate regressions are caught in CI. Any zero-tolerance family failure
fails this test.
"""

from __future__ import annotations

from app.evals import EVAL_VERSION, run_evals


def test_eval_suite_release_gate_not_blocked():
    report = run_evals()
    gate = report["release_gate"]
    assert gate["blocked"] is False, (
        f"eval release gate blocked by families: {gate['blocking_families']}"
    )


def test_eval_suite_all_families_ok():
    report = run_evals()
    bad = [f["family"] for f in report["families"] if not f["ok"]]
    assert not bad, f"eval families with failures: {bad}"


def test_eval_report_version_and_counts():
    report = run_evals()
    meta = report["meta"]
    assert meta["eval_version"] == EVAL_VERSION
    assert meta["generated_at"] == "2026-09-03"
    assert meta["passed"] >= meta["total"] - meta["failed"]
    # At least the four core families must be exercised.
    families = {f["family"] for f in report["families"]}
    assert {"guardrails", "authorization", "rag", "privacy"} <= families


def test_eval_report_has_no_failure_records():
    report = run_evals()
    for fam in report["families"]:
        assert fam["failures"] == [], f"{fam['family']} recorded unexpected failures"
