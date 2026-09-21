"""Run the prompt battery against the real user-chat route and emit a JSON,
versioned evidence report. Mirrors the `python -m app.evals` pattern but for
the live route-level battery (specific + vague prompts).

Usage (from backend/):
    python -m tests.prompt_battery_report            # prints to stdout
    python -m tests.prompt_battery_report --out r.json

The model client is substituted (offline), exactly like the pytest battery in
tests/test_prompt_battery.py; everything else is the production code path.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.core.rate_limit import chat_limiter
from app.db.session import get_db
from app.main import app
from tests.test_prompt_battery import battery_evidence


def _build_in_memory_testclient():
    """Reuse the eval-style in-memory SQLite wiring (no pytest fixtures)."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.dialects.postgresql import ARRAY
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import json as _json

    def _array_bind_processor(self, dialect):  # noqa: ARG001
        def process(value):
            return None if value is None else _json.dumps(value)

        return process

    def _array_result_processor(self, dialect, coltype):  # noqa: ARG001
        def process(value):
            return None if value is None else _json.loads(value)

        return process

    def _compile_array_sqlite(self, type_, **kw):  # noqa: ARG001
        return "TEXT"

    if not getattr(SQLiteTypeCompiler, "visit_ARRAY", None):
        SQLiteTypeCompiler.visit_ARRAY = _compile_array_sqlite
    ARRAY.bind_processor = _array_bind_processor
    ARRAY.result_processor = _array_result_processor

    from app.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _rec):
        dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    # Mirror conftest: migration 0018 CHECK constraint via trigger (create_all
    # does not run Alembic migrations).
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TRIGGER IF NOT EXISTS ck_chat_conversations_one_actor "
            "INSERT ON chat_conversations "
            "BEGIN "
            "  SELECT CASE "
            "    WHEN (NEW.admin_id IS NOT NULL AND NEW.user_id IS NOT NULL) "
            "      OR (NEW.admin_id IS NULL AND NEW.user_id IS NULL) "
            "    THEN RAISE(ABORT, 'Exactly one of admin_id or user_id must be set') "
            "  END; "
            "END"
        ))
        conn.commit()
    Session = sessionmaker(bind=engine)
    db = Session()

    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    return client, db


def main() -> int:
    parser = argparse.ArgumentParser(description="Zoiko Rooms prompt battery report")
    parser.add_argument("--out", help="Write the JSON report to this file")
    args = parser.parse_args()

    chat_limiter.reset()
    client, db = _build_in_memory_testclient()
    try:
        evidence = battery_evidence(client, db)
    finally:
        client.close()
        db.close()

    report = {
        "meta": {
            "name": "user-chat prompt battery",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cases": len(evidence),
            "passed": sum(1 for e in evidence if e["passed"]),
            "failed": sum(0 if e["passed"] else 1 for e in evidence),
            "vague_cases": sum(1 for e in evidence if e["vague"]),
            "specific_cases": sum(1 for e in evidence if not e["vague"]),
        },
        "cases": evidence,
        "model_client": "mocked (offline) — see tests/test_prompt_battery.py",
    }
    text = json.dumps(report, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"report written to {args.out}")
    else:
        print(text)

    failed = report["meta"]["failed"]
    print(
        f"\nbattery: {report['meta']['passed']}/{report['meta']['cases']} passed "
        f"(specific={report['meta']['specific_cases']}, vague={report['meta']['vague_cases']})"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())