"""Copies any file still on the server's disk into the stored_files table and
reports what it did. The alembic migration 0038_stored_files already runs this
once; run it again (from backend/) any time -- files already copied are
skipped:

    python copy_files_to_database.py

Exits non-zero if any copied file failed its hash check."""

from __future__ import annotations

import sys

from app.db.session import engine
from app.services.file_copy import copy_disk_files_to_database


def main() -> int:
    with engine.begin() as connection:
        report = copy_disk_files_to_database(connection)
    mismatched = 0
    for category, counts in report.items():
        print(f"{category:24} copied {counts['copied']:6}  already there {counts['skipped']:6}  mismatched {counts['mismatched']}")
        mismatched += counts["mismatched"]
    if mismatched:
        print(f"{mismatched} file(s) did not match after copying -- see the log.")
        return 1
    print("OK: every file on disk is in the database.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
