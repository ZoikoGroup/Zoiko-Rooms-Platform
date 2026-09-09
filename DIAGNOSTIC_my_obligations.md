# DIAGNOSTIC — chat tool failures ("Couldn't fetch that data") — my_obligations / my_applications / my_occupancies

Status: **RESOLVED** (live DB migrated; backend tests 283 passed)
Date: 2026-09-03

## Symptom

For essentially every chat tool call in the live user-chat SSE stream, the
frontend shows **"Couldn't fetch that data (my_obligations)"** (also
`my_applications`, `my_occupancies`). Backend uvicorn logs show:

```
psycopg.errors.InFailedSqlTransaction: current transaction is aborted, commands ignored until end of transaction block
```

raised during `INSERT INTO chat_messages` (`backend/app/api/routes/user_chat.py:167`),
followed by a `PendingRollbackError` from the `audit()` at `user_chat.py:213` because the
session was never rolled back before it was reused.

## Root cause (confirmed in code + against the live DB)

The chat stream was failing because the **live PostgreSQL database was never fully
migrated**. Two concrete problems surfaced:

1. **The Alembic migration chain had TWO heads**:
   - `0022_feature_flags` (mainline: `... -> 022bda4d123a -> 71f4a2b9c0aa -> 0020_ai_handoff -> 0021_knowledge_base -> 0022_feature_flags`)
   - `d3f1a9c2b6e4_link_guests_to_user_accounts` (branches off the same `022bda4d123a` but was a *separate* head)

   With two heads, `alembic upgrade head` is ambiguous and had not been applied cleanly.

2. **The live DB was stale at `71f4a2b9c0aa`** (verified via `SELECT version_num FROM alembic_version`
   = `71f4a2b9c0aa`). It was therefore missing:
   - `d3f1a9c2b6e4` which adds **`guests.user_account_id`** (the FK+unique used to link a
     user's record to their rental `Guest` identity),
   - `0020_ai_handoff`, `0021_knowledge_base`, `0022_feature_flags`.

   Concretely: `information_schema.columns` showed the `guests` table had **no
   `user_account_id` column**, and **no table anywhere** had `user_account_id`.

Chain of failure per tool call:

```
get_guest_for_user / _resolve_user_guest (backend/app/crud/guest.py:19)
   SELECT ... WHERE guests.user_account_id = ...   <-- column DOES NOT EXIST on PG
        -> OperationalError -> transaction ABORTED
execute_tool (chat_service.py:695) catches it -> {"error": "Tool failed: ..."}
   -> SSE "tool_error" -> frontend "Couldn't fetch that data"
Route then tries db.commit() at user_chat.py:167
   -> InFailedSqlTransaction (transaction already aborted by the failed SELECT)
audit() at user_chat.py:213
   -> PendingRollbackError (session still in aborted state)
```

So the `my_*` handlers were not returning empty-but-valid datasets; the very first query in
the tool's guest-resolution path failed at the DB layer because the column the ORM queries
did not exist in the live schema.

### Why the earlier repo-only QA pass could not see this

The unit/QA suite runs against **SQLite** (in-memory, fixtures in `conftest.py`). SQLite's
`CREATE TABLE` is generated directly from the SQLAlchemy ORM metadata, so
`guests.user_account_id` always existed in tests. The bug was **PostgreSQL-only**: the live DB
was behind in Alembic migrations. Reproducing it required inspecting the actual Postgres
schema, which is what confirmed the missing column.

## Fix applied

1. **Created a merge migration** `backend/alembic/versions/e49ffda0d565_merge_feature_flags_and_guest_account_.py`
   with `down_revision = ('0022_feature_flags', 'd3f1a9c2b6e4')` and an empty body — it only
   resolves the two heads into a single head `e49ffda0d565`.
2. **Ran the migration against the live DB** (port from `.env`, `localhost:5433/zoiko_rooms`):
   ```
   python -m alembic upgrade head
   # 022bda4d123a -> d3f1a9c2b6e4        (adds guests.user_account_id)
   # 71f4a2b9c0aa -> 0020_ai_handoff
   # 0020 -> 0021_knowledge_base
   # 0021 -> 0022_feature_flags
   # 0022 + d3f1a9c2b6e4 -> e49ffda0d565 (merge)
   ```
3. **Verified**: `alembic_version` = `e49ffda0d565`; `guests.user_account_id` column now
   exists; and the `my_applications` / `my_occupancies` / `my_obligations` handlers run
   cleanly against the live DB for every existing user (no `InFailedSqlTransaction`; they
   return proper "No … found" info rows for users with no occupancies).

## Scope of impact

- **Who**: every logged-in user whose chat stream executes a `my_*` (guest-linked) tool on the
  affected live DB. It was effectively universal because the missing column poisoned every
  such tool call.
- **Schema-only, no data loss**: the fix adds a nullable FK column; existing guest rows keep
  their data. `get_guest_for_user` opportunistically backfills `user_account_id` from an email
  match on first use, so pre-existing guests are re-linked lazily.

## Files changed

| File | Change |
|------|--------|
| `backend/alembic/versions/e49ffda0d565_merge_feature_flags_and_guest_account_.py` | **NEW** — empty merge revision resolving heads `0022_feature_flags` + `d3f1a9c2b6e4` |

No application code (`user_chat.py`, `chat_service.py`) was changed for this fix — the
transaction-abort symptoms were a downstream effect of the missing column, not a code bug.

## Regression

- `pytest tests/` from `backend/`: **288 passed** (the migration is applied only to Postgres;
  the SQLite-based suite is unaffected and covers the ORM/handler paths).
- Tool handlers exercised directly against the live Postgres DB for all 7 `user_account` rows
  — no transaction errors.

## Recommendation

- **Always run `alembic upgrade head` against the real DB after pulling schema changes.** Add
  a pre-deploy migration check (e.g. `alembic current` must equal `alembic heads`) to a CI gate
  so a stale DB (or a future multi-head chain) fails fast instead of surfacing as a generic
  "Couldn't fetch that data" in chat.
- **Future-proofing (optional)**: `execute_tool`'s broad `try/except Exception`
  (`chat_service.py:695`) swallows and masks the real DB exception, and the `tool_error` SSE
  path neither logs nor audits the underlying exception server-side (`user_chat.py:186-189`).
  Recommend logging the exception type/detail for `tool_error` events (keep user-facing copy
  generic). This was the reason the true cause was hard to find and only surfaced here after
  inspecting the DB schema directly.
