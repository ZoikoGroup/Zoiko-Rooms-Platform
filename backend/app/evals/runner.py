"""Deterministic evaluation runner (ZR-AI-EVAL-001).

Runs the golden ``cases`` against the real implemented subsystems and produces a
versioned, machine-readable release report. Families tagged zero_tolerance are
release-blocking gates: any single failure fails the release.

The runner is framework-agnostic -- it builds its own in-memory SQLite engine so
it can be driven from pytest, a CLI, or a CI step without depending on test
fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.evals.cases import (
    AUTH_CASES,
    AUTH_INVARIANTS,
    DETERMINATION_CASES,
    HANDOFF_CASES,
    PRIVACY_CASES,
    PRIVACY_INVARIANTS,
    RAG_CASES,
    RAG_INVARIANTS,
    RISK_CASES,
    TIER_CASES,
)
from app.models.admin_user import AdminUser
from app.models.kb import KbChunk, KbDocument, KbRelease
from app.models.listing import Listing
from app.models.user_account import UserAccount
from app.services.guardrails import classify_action_tier, classify_risk, scan_for_determination
from app.services.handoff import handoff_requested
from app.services.kb import ingest_document, make_active, revoke_document
from app.services.pdp import Decision, check_permission
from app.services.rag import resolve_citation, retrieve

EVAL_VERSION = "1.0.0"
GENERATED_AT = "2026-09-03"


@dataclass
class FamilyResult:
    family: str
    zero_tolerance: bool
    passed: int = 0
    failed: int = 0
    failures: list[dict] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def record(self, data: dict, ok: bool, label: str) -> None:
        if ok:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append({"label": label, **data})


def _make_session() -> Session:
    _patch_sqlite_array()
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, _rec):
        dbapi_conn.cursor().execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return Session(bind=engine)


def _patch_sqlite_array() -> None:
    """Make Postgres ARRAY columns render/round-trip as JSON text on SQLite.

    The Evaluation harness always runs against an in-memory SQLite engine, so
    this mirrors the pytest conftest patch without depending on pytest imports.
    Idempotent and safe: it only affects the SQLite type compiler.
    """
    import json

    from sqlalchemy.dialects.postgresql import ARRAY
    from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

    if getattr(SQLiteTypeCompiler, "visit_ARRAY", None) is None:

        def _compile_array_sqlite(self, type_, **kw):
            return "TEXT"

        def _array_bind_processor(self, dialect):
            def process(value):
                return None if value is None else json.dumps(value)

            return process

        def _array_result_processor(self, dialect, coltype):
            def process(value):
                return None if value is None else json.loads(value)

            return process

        SQLiteTypeCompiler.visit_ARRAY = _compile_array_sqlite  # type: ignore[attr-defined]
        ARRAY.bind_processor = _array_bind_processor  # type: ignore[assignment]
        ARRAY.result_processor = _array_result_processor  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers to seed deterministic DB state
# ---------------------------------------------------------------------------


def _admin(db: Session, i: int, role: str) -> AdminUser:
    a = AdminUser(
        id=i,
        email=f"admin{i}@test.com",
        hashed_password="x",
        full_name=f"Admin {i}",
        role=role,
        is_active=True,
        approval_status="approved",
    )
    db.add(a)
    db.flush()
    return a


def _user(db: Session) -> UserAccount:
    u = UserAccount(email="eval@user.com", hashed_password="x", full_name="Eval User", is_active=True, email_verified=True)
    db.add(u)
    db.flush()
    return u


def _listing(db: Session, lid: str, owner_id: int | None, state: str = "PUBLISHED") -> Listing:
    l = Listing(
        id=lid,
        slug=f"eval-{lid.lower()}",
        name=f"Room {lid}",
        room_type="single_occupancy",
        city="Mumbai",
        location="Andheri",
        price_per_night=1000,
        currency="INR",
        guests=1,
        state=state,
        owner_id=owner_id,
    )
    db.add(l)
    db.flush()
    return l


def _kb_publish(db: Session, doc_id: int, market: str) -> None:
    make_active(db, doc_id)
    rel = KbRelease(version="eval-v1", market=market, status="ACTIVE")
    db.add(rel)
    db.flush()
    doc = db.get(KbDocument, doc_id)
    doc.release_id = rel.id
    db.flush()


# ---------------------------------------------------------------------------
# Family runners
# ---------------------------------------------------------------------------


def _eval_guardrails() -> FamilyResult:
    r = FamilyResult("guardrails", zero_tolerance=True)

    for case in RISK_CASES:
        actual = classify_risk(case.input).value
        ok = actual == case.expected
        r.record({"case": case.input[:40], "expected": case.expected, "actual": actual}, ok, f"classify_risk({case.note})")

    for case in TIER_CASES:
        actual = classify_action_tier(case.input).value
        ok = actual == case.expected
        r.record({"case": case.input[:40], "expected": case.expected, "actual": actual}, ok, f"classify_action_tier({case.note})")

    for case in DETERMINATION_CASES:
        actual = scan_for_determination(case.input).blocked
        ok = actual == case.expected
        r.record({"case": case.input[:40], "expected": case.expected, "actual": actual}, ok, "scan_for_determination")

    for case in HANDOFF_CASES:
        actual = handoff_requested(case.input)
        ok = actual == case.expected
        r.record({"case": case.input[:40], "expected": case.expected, "actual": actual}, ok, "handoff_requested")

    r.invariants = [
        "Deterministic risk (R0-R4), action tier (A1-A3) override model classification",
        "Determination assertions are always blocked server-side regardless of prompt wording",
        "Human-escalation language is detected deterministically",
    ]
    return r


def _eval_authorization() -> FamilyResult:
    r = FamilyResult("authorization", zero_tolerance=True)
    db = _make_session()
    try:
        owner = _admin(db, 1, "admin")
        non_owner = _admin(db, 2, "admin")
        super_admin = _admin(db, 99, "super_admin")
        user = _user(db)
        _listing(db, "L1", 1)
        _listing(db, "L2", 1)
        _listing(db, "PUBS", None, "PUBLISHED")
        _listing(db, "DRAFT", None, "DRAFT")

        from app.services.chat_service import TOOL_REGISTRY

        def permit(actor, tool_name, args):
            spec = TOOL_REGISTRY[tool_name]
            return check_permission(actor, spec, args, db).result == Decision.PERMIT

        table = {
            ("user", "search_platform"): (user, "search_platform", {}, False),
            ("user", "list_bookings"): (user, "list_bookings", {}, False),
            ("admin", "search_listings"): (non_owner, "search_listings", {}, False),
            ("admin", "list_bookings"): (non_owner, "list_bookings", {}, False),
            ("super_admin", "list_bookings"): (super_admin, "list_bookings", {}, True),
            ("admin_owner", "get_listing:L1"): (owner, "get_listing", {"listing_id": "L1"}, True),
            ("admin_non_owner", "get_listing:L2"): (non_owner, "get_listing", {"listing_id": "L2"}, False),
            ("super_admin", "get_listing:L2"): (super_admin, "get_listing", {"listing_id": "L2"}, True),
            ("user", "get_listing_details:PUBS"): (user, "get_listing_details", {"listing_id": "PUBS"}, True),
            ("user", "get_listing_details:DRAFT"): (user, "get_listing_details", {"listing_id": "DRAFT"}, False),
        }

        for group in (
            "user_cannot_admin_tool",
            "admin_cannot_user_tool",
            "super_admin_only_gate",
            "cross_account_listing",
            "user_listing_state",
        ):
            for case in AUTH_CASES[group]:
                _run_table(r, table, case.input, case.expected, permit)

        r.invariants = AUTH_INVARIANTS
    finally:
        db.close()
    return r


def _run_table(r: FamilyResult, table: dict, key: Any, expected: bool, permit_fn: Callable) -> None:
    actor, tool_name, args, _ = table[key]
    actual = permit_fn(actor, tool_name, args)
    ok = actual == expected
    r.record({"case": key, "expected": expected, "actual": actual}, ok, f"authorize({key})")


def _eval_rag() -> FamilyResult:
    r = FamilyResult("rag", zero_tolerance=True)
    db = _make_session()
    try:
        # Active release retrievable
        doc_id = ingest_document(db, slug="active-doc", title="Deposit Rules", content="deposit protection rules 2026", market="GLOBAL", domain="tenancy").document_id
        _kb_publish(db, doc_id, "GLOBAL")
        hits = retrieve(db, "deposit rules")
        ok = len(hits) == 1
        r.record({"case": "deposit", "expected": True, "actual": ok}, ok, "only_active_release retrievable")

        # Unpublished doc not retrievable (separate doc, no release)
        ingest_document(db, slug="unpublished", title="Unpublished", content="zeta unpublished protocol 2026", market="GLOBAL")
        ok = retrieve(db, "zeta unpublished") == []
        r.record({"case": "deposit-unpublished", "expected": False, "actual": ok}, ok, "unpublished not retrievable")

        # Revocation
        rev_doc = ingest_document(db, slug="notice-doc", title="Notice", content="notice period rules 2026", market="GLOBAL").document_id
        _kb_publish(db, rev_doc, "GLOBAL")
        revoke_document(db, rev_doc)
        ok = retrieve(db, "notice period") == []
        r.record({"case": "notice", "expected": False, "actual": ok}, ok, "revoked not retrieved (RES-011)")

        # Market filter
        eng_doc = ingest_document(db, slug="rtr-doc", title="Right to Rent", content="right to rent england rules", market="ENGLAND", domain="tenancy").document_id
        _kb_publish(db, eng_doc, "ENGLAND")
        ok = retrieve(db, "right to rent", market="GLOBAL") == []
        r.record({"case": "right_to_rent", "expected": False, "actual": ok}, ok, "market filter excludes England")

        # Expired
        stale_doc = ingest_document(db, slug="stale-doc", title="Stale", content="stale guidance 2020", market="GLOBAL", expiry_date=date(2021, 1, 1)).document_id
        _kb_publish(db, stale_doc, "GLOBAL")
        ok = retrieve(db, "stale guidance") == []
        r.record({"case": "stale", "expected": False, "actual": ok}, ok, "expired doc excluded")

        # Citation validator
        cit_ok = resolve_citation(db, "kb:1:999999") is None
        r.record({"case": "kb:1:999999", "expected": False, "actual": cit_ok}, cit_ok, "fabricated citation rejected")
        malformed_ok = resolve_citation(db, "not-acitation") is None
        r.record({"case": "not-acitation", "expected": False, "actual": malformed_ok}, malformed_ok, "malformed citation rejected")

        r.invariants = RAG_INVARIANTS
    finally:
        db.close()
    return r


def _eval_privacy() -> FamilyResult:
    r = FamilyResult("privacy", zero_tolerance=True)
    db = _make_session()
    try:
        user = _user(db)
        from app.models.chat import ChatConversation, ChatMessage
        from app.services.handoff import build_handoff_packet

        for case in PRIVACY_CASES:
            content = case.expected
            conv = ChatConversation(user_id=user.id)
            db.add(conv)
            db.flush()
            db.add(ChatMessage(conversation_id=conv.id, role="assistant", content=content))
            db.flush()
            packet = build_handoff_packet(conv, reason_code="USER_REQUEST", urgency=None, summary=None, request_text="talk to human")
            manifest = packet.to_manifest()
            excerpt = manifest["conversation_excerpt"].lower()
            leak = ("password" in excerpt) or ("api_key" in excerpt) or ("secret" in excerpt)
            ok = not leak
            r.record({"case": case.input, "content": content[:30], "leak": leak}, ok, f"privacy({case.note})")

        r.invariants = PRIVACY_INVARIANTS
    finally:
        db.close()
    return r


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_evals() -> dict[str, Any]:
    families = [
        _eval_guardrails(),
        _eval_authorization(),
        _eval_rag(),
        _eval_privacy(),
    ]
    blocking = [f for f in families if f.zero_tolerance and not f.ok]
    total = sum(f.passed + f.failed for f in families)
    passed = sum(f.passed for f in families)
    failed = total - passed
    report = {
        "meta": {
            "eval_version": EVAL_VERSION,
            "generated_at": GENERATED_AT,
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "families": len(families),
            "passed": passed,
            "failed": failed,
            "total": total,
        },
        "families": [
            {
                "family": f.family,
                "zero_tolerance": f.zero_tolerance,
                "passed": f.passed,
                "failed": f.failed,
                "ok": f.ok,
                "invariants": f.invariants,
                "failures": f.failures,
            }
            for f in families
        ],
        "release_gate": {
            "blocked": len(blocking) > 0,
            "blocking_families": [f.family for f in blocking],
            "doctrine": "Zero-tolerance: any failure in safety/privacy/authorization/transaction-integrity fails release.",
        },
    }
    return report


def report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)
