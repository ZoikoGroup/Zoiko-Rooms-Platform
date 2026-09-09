"""CLI: run the deterministic evaluation suite and emit a versioned JSON report.

Usage (from backend/):
    python -m app.evals                 # prints report to stdout
    python -m app.evals --out report.json   # writes a JSON file
Exit code is non-zero if any zero-tolerance gate blocks the release.
"""

from __future__ import annotations

import argparse
import sys

from app.evals import report_json, run_evals


def main() -> int:
    parser = argparse.ArgumentParser(description="Zoiko Rooms AI evaluation runner")
    parser.add_argument("--out", help="Write the JSON report to this file")
    args = parser.parse_args()

    report = run_evals()
    text = report_json(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"report written to {args.out}")
    else:
        print(text)

    blocked = report["release_gate"]["blocked"]
    print(f"\nrelease gate: {'BLOCKED' if blocked else 'PASS'} "
          f"({report['meta']['passed']}/{report['meta']['total']} passed)")
    if blocked:
        print("blocking families:", ", ".join(report["release_gate"]["blocking_families"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
