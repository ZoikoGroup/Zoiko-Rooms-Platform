# Zoiko Rooms AI Assistant — Implementation Progress

Running paper-trail for the requirements round (Phases 1–7). Each phase records
what was implemented, files touched, tests added, and any deviations from the
spec docs (with justification). Source of truth for re-running the compliance
audit.

Status legend per phase: ✅ done · 🟡 in progress · ⬜ not started

---

## Phase 1 — Security fixes — ✅

### What was implemented
1. **`COOKIE_SECURE` / production guard.** Added an `environment` setting
   (`backend/app/core/config.py`) defaulting to `development`. A
   `model_validator(mode="after")` fails loudly at settings load (i.e. at
   import/boot) whenever `ENVIRONMENT=production` and any of the following hold:
   - `COOKIE_SECURE` is false
   - `JWT_SECRET` is missing, a known placeholder, or shorter than 32 chars
   - `SEED_ADMIN_PASSWORD` is missing, a known placeholder, or shorter than 12 chars

   Development environments are unaffected (default is `development`).
2. **Weak defaults.** Identified the dev placeholder defaults in `config.py`
   (`jwt_secret="dev-secret-change-me"`, `seed_admin_password="change-this-password"`).
   Left them as *dev* defaults (so local boot still works) but they are now
   refused in production by the validator above. `backend/.env` already carries
   real (non-placeholder) values and is **not** committed; no secret values were
   written to the repo.
3. **Rate limiting on the chat SSE endpoints.** New `backend/app/core/rate_limit.py`
   provides a thread-safe in-process fixed-window `RateLimiter` plus a module-level
   `chat_limiter` (limits configurable via `settings.chat_rate_limit_max` /
   `chat_rate_limit_window_seconds`). Wired into `/api/admin/chat/.../stream` and
   `/api/users/chat/.../stream` (keyed `admin:{id}` / `user:{id}`), returning
   HTTP 429 when exceeded. This no longer relies solely on the Groq provider's
   own limit.
4. **Error-leak prevention (verified).** Confirmed the existing `_log_failure` +
   `ChatServiceError.log_detail` pattern already surfaces only hardcoded, safe
   user copy over SSE. Added tests proving arbitrary internals/secrets never
   reach the client.

### Files touched
- `backend/app/core/config.py` — added `environment`, `chat_rate_limit_max`,
  `chat_rate_limit_window_seconds`, production validator, `is_production`.
- `backend/app/core/rate_limit.py` — new module.
- `backend/app/api/routes/chatbot.py` — rate-limit guard on stream endpoint.
- `backend/app/api/routes/user_chat.py` — rate-limit guard on stream endpoint.
- `backend/tests/test_security_config.py` — new.
- `backend/tests/test_rate_limit.py` — new.
- `backend/tests/test_error_leak.py` — new.

### Tests added
- `test_security_config.py` (6): production refused when cookie not secure /
  placeholder JWT / short JWT / placeholder admin password; production loads
  with secure config; development still allows placeholders.
- `test_rate_limit.py` (7): unit window/key-isolation/reset + integration 429
  on both admin and user stream endpoints.
- `test_error_leak.py` (2): admin + user SSE `error` event contains no secrets,
  env-var names, tracebacks, or provider internals.

### Result
`pytest backend/` → **85 passed** (70 pre-existing + 15 new). No regressions.

### Deviations from spec
- Spec calls for an "edge" rate control (Internet Facing Edge / WAF/bot/rate).
  In-process limiter was chosen because the repo has no service mesh/edge/WAF
  config and no Redis. Interface is intentionally swappable for a shared store
  if a multi-node deployment appears. Noted as a deployment concern.
- The production guard is enforced at config load rather than a separate
  startup hook in `main.py`; it fails earlier and covers every entrypoint.

---

## Phase 2 — Deterministic guardrail enforcement — ✅

### What was implemented
Per `ZR-AI-PG-001` (§7 A0–A3, §8 output validation, §9.3 no legal-substitution,
§12.3 decision metadata) and `ZR-AI-ARCH-001` (deterministic risk/action gates,
deterministic rules override model classification):

1. **Action-tier engine (A1–A3).** New `backend/app/services/guardrails.py`
   provides a deterministic, server-side `classify_action_tier(text)` ->
   `A1 | A2 | A3`:
   - `A1` informational/safe,
   - `A2` needs clarification (e.g. jurisdiction-dependent legal question with no
     market context),
   - `A3` requires human confirmation (crisis, determination/authority request,
     or high-consequence decision topics: compliance, right-to-rent,
     eligibility, eviction, dispute, discrimination). \
   The tier is computed per turn in `chat_service.stream_assistant_reply` from
   the incoming user text and surfaced on the SSE **done** payload as
   `meta.action_tier`.
2. **"No determinations" rule (code-enforced output gate).** A deterministic
   `scan_for_determination(text)` scanner inspects the assembled assistant output
   before the final `done` event. If it asserts an eligibility/compliance/
   approval/tenancy decision ("you are approved/eligible/entitled/qualified",
   "your application is approved", etc.), the service appends a corrective
   notice block, sets `meta.determination_blocked = True`, and the routes emit a
   `chat.guardrail.determination_blocked` / `user_chat.guardrail...` AuditEvent.
   This is a deterministic backstop independent of system-prompt wording.
3. **Server-authoritative R0–R4.** Ported the client `src/lib/risk.ts` R0–R4
   logic into server-side `classify_risk` / `risk_topic_name`. The server
   computes `risk` + `risk_topic` and returns them in the SSE **done** payload;
   the frontend now **trusts** the server value (overriding its own client-side
   guess) and renders an amber Layer-C warning for R2/R3/R4 and a red
   review-required note for A3 / determination-blocked.

### Files touched
- `backend/app/services/guardrails.py` — new module (classifiers + scanner).
- `backend/app/services/chat_service.py` — compute risk/tier/topic per turn;
  run determination scanner; append notice; add guardrail keys to `done` meta.
- `backend/app/api/routes/chatbot.py`, `backend/app/api/routes/user_chat.py` —
  surface `guardrail` on the `done` SSE event; audit
  `*.guardrail.determination_blocked`.
- `src/lib/chat.ts`, `src/lib/user-chat.ts` — `Guardrail` types on `done` event.
- `src/components/admin/chat/AdminChatPanel.tsx`,
  `src/components/user/chat/UserChatPanel.tsx` — trust server risk/action-tier,
  enhanced Layer-C + A3 notice banners.
- `backend/tests/test_guardrails.py` — new.

### Tests added (`test_guardrails.py`, 22)
- Unit: `classify_risk` (R0/R2/R3/R4), `risk_topic_name`, `classify_action_tier`
  (A1/A2/A3 incl. jurisdiction-without-market->A2), `scan_for_determination`.
- Service integration (real `stream_assistant_reply`, mocked Groq): A3 query
  flagged; determination-assertion output gets notice + `determination_blocked`;
  clean output not blocked; SSE `done` payload matches server classification.

### Result
`pytest backend/` → **107 passed** (85 + 22 new). `tsc --noEmit` clean;
eslint clean on changed files. No regressions.

### Deviations from spec
- Spec models A0 also; the task scoped `A1–A3`. `A1` in this implementation
  covers informational + account-assist reads; `A0` is not a separate tier here.
- Fully buffering the response to *prevent* streaming a determination before it
  leaves the server would break the streaming UX (all other chat features stream
  token-by-token). Chosen compromise: the deterministic gate runs on the
  assembled final text at the `done` point — it appends the corrective notice,
  sets the `determination_blocked` flag (frontend shows a red "review required"
  banner), and audits. A fully pre-stream gate can be added later without
  interface changes.
- R0–R4 mirrors `risk.ts` exactly (R1 not currently emitted by either side; kept
  for schema compatibility).

## Phase 3 — RBAC / ABAC hardening — ✅

### What was implemented
Per `ZR-AI-AUTH-001` (hybrid RBAC+ABAC+ReBAC, AUTH-I-005 object re-check,
AUTH-I-007 function re-check, §19 PDP decision contract):

1. **Policy Decision Point (PDP).** New `backend/app/services/pdp.py` exposes a
   deterministic, model-independent `check_permission(actor, tool, args, db)`
   returning an `AuthorizationDecision` (`PERMIT|DENY|NOT_APPLICABLE|CHALLENGE`
   + a stable reason code + `policy_version`). The PEP
   (`chat_service.execute_tool`) calls it **before** any tool handler runs, so
   `allowed=False` short-circuits the call instead of the model trusting a
   client-supplied value.
2. **RBAC layer (backward compatible).** Replaced the previous inline role checks
   inside `execute_tool` with the PDP's RBAC evaluation: the actor's role
   (`user` / `admin` / `super_admin`) must be in the tool's `roles` and must clear
   `super_admin_only`. Identical semantics to the old code, verified by the
   pre-existing `TestToolGating` tests.
3. **ABAC/ReBAC guards.** Added a declarative `permission` field on `ToolSpec`;
   tools opt into attribute/relationship guards registered in
   `PERMISSION_GUARDS`:
   - `listing.detail` (`get_listing` admin tool): re-checks the **authoritative**
     relationship every call — super_admin permits; non-super admin permits only
     if `listing.owner_id == actor.id`; otherwise
     `AUTH_OBJECT_RELATIONSHIP_MISSING`. This is the cross-account rule: a host
     accessing another host's (non-published) listing is denied even though the
     role permits the tool generally.
   - `listing.read_published` (`get_listing_details` user tool): state guard —
     permits only if the listing exists **and** is `PUBLISHED`; draft/paused are
     denied (`AUTH_PROPERTY_DENIED`). Non-existent resources →
     `AUTH_RESOURCE_NOT_FOUND`.
4. Both guards resolve the target listing from the tool args and query the DB
   at decision time, satisfying the object/function re-check invariants.

### Files touched
- `backend/app/services/pdp.py` — new (Decision, AuthorizationDecision,
  `check_permission`, RBAC + two ABAC guards).
- `backend/app/services/chat_service.py` — added `permission` on `ToolSpec`;
  set it on `get_listing` + `get_listing_details`; `execute_tool` now routes
  through `check_permission`; added `_parse_args` helper.
- `backend/tests/test_pdp.py` — new (19 tests).

### Tests added (`test_pdp.py`, 19)
- RBAC: role gating (user↛admin tool, admin↛user tool, non-super↛
  super_admin-only, super_admin✅), no-permission tools skip ABAC.
- `listing.detail`: owner✅, **cross-account non-owner denied**, super_admin✅,
  missing/non-existent→`AUTH_RESOURCE_NOT_FOUND`.
- `listing.read_published`: published✅, draft/paused denied, missing denied.
- `execute_tool` integration: cross-account denied through the PEP, owner
  permitted, user draft denied / published permitted.

### Result
`pytest backend/` → **126 passed** (107 + 19 new). No regressions.

### Deviations from spec
- `CHALLENGE` and `NOT_APPLICABLE` enum members exist for contract fidelity but
  the current tool set only ever returns `PERMIT`/`DENY`; no interactive
  challenge step is wired yet (Phase 4 human handoff will consume any future
  CHALLENGE).
- Market/jurisdiction attribute checks are designed-in (guards take the raw args
  and a `db` session) but not populated — jurisdiction packs arrive in Phase 5.

## Phase 4 — Human handoff subsystem — ✅

### What was implemented
Per Conversation Flow §15 (handoff lifecycle), API §15 (handoff endpoints),
ERD `ai_handoff`, and FRS-HO-001..004:

1. **`ai_handoff` model.** New `backend/app/models/handoff.py` (`AiHandoff`,
   table `ai_handoffs`) matching the ERD columns: `conversation_id`,
   `reason_code`, `urgency`, `support_case_ref`, `shared_context_manifest`,
   `status`, timestamps — plus `initiator_user_id`, `summary`, `consent_state`
   and `bridge_messages_json` (additive, see deviations).
2. **Minimum-necessary packet builder (FRS-HO-001/-002).** New
   `backend/app/services/handoff.py` — `build_handoff_packet` produces the
   user-visible summary, relevant resource IDs (`L##` references), recent
   action/error status, and a sanitized conversation excerpt. The excerpt only
   includes user/assistant text (never tool JSON, `meta_json`, prompts or
   reasoning) and strips any line mentioning a secret/prompt (`password`,
   `secret`, `api_key`, `authorization`, `system prompt`, …).
3. **Consent/notice (FRS-HO-003).** `consent_state` is `NOTICE_GIVEN` for
   explicit user requests and `REVIEW_REQUIRED` for policy-triggered reasons.
4. **Never-fabricate case ref (FRS-HO-004).** `support_case_ref` is always `NULL`
   on creation — only an authoritative external support system can set it; the
   Assistant only ever records a governed `REQUESTED` handoff.
5. **Endpoints** (new `backend/app/api/routes/handoffs.py`, prefix
   `/api/users/chat/handoffs`, user-auth):
   - `POST ""` create (`reasonCode` validated against controlled taxonomy, 409 on
     duplicate active handoff), `GET /{id}`, `POST /{id}/messages` (bridge
     message; `REQUESTED` only), `POST /{id}/cancel` (pending → `CLOSED`).
   - Resource ownership re-checked per call (`initiator_user_id == actor.id`).
6. **Handoff detector wired into the stream.** `handoff_requested(text)` (a
   deterministic regex) surfaces a `handoffSuggested` flag on the user-chat SSE
   `done` event, so the UX can offer human escalation when the user asks for it.
7. **Audit.** `user_chat.handoff.created` / `.cancelled` AuditEvents recorded.

### Files touched
- `backend/app/models/handoff.py` — new `AiHandoff` model + controlled taxonomies.
- `backend/app/services/handoff.py` — new (detector, packet builder, create/
  cancel/bridge helpers, audit).
- `backend/app/api/routes/handoffs.py` — new router.
- `backend/app/main.py` — register `handoffs.router`.
- `backend/app/api/routes/user_chat.py` — emit `handoffSuggested` on `done`.
- `backend/alembic/versions/0020_ai_handoff.py` — new migration (head `0020_ai_handoff`).
- `backend/tests/test_handoff.py` — new (17 tests).

### Tests added (`test_handoff.py`, 17)
- Detector: human-request phrases trigger, normal questions don't.
- Packet: sanitization strips secrets/prompts, resource IDs collected, summary
  defaults, urgency escalation (SAFETY→SAFETY_CRITICAL, DISPUTE→HIGH), consent
  state per reason.
- Service: create + `support_case_ref is None`, duplicate-active rejected,
  cancel, bridge-message gating.
- Routes: create→read→cancel flow, cross-user isolation (404), bridge endpoint,
  invalid reason (422), duplicate create (409), unauthenticated (401).

### Result
`pytest backend/` → **143 passed** (126 + 17 new). Alembic head is single
(`0020_ai_handoff`). No regressions.

### Deviations from spec
- ERD specifies `id uuid` for `ai_handoff`; this codebase uses integer PKs
  throughout (every other table), so `AiHandoff.id` is an integer. The exposed
  `support_case_ref` remains the opaque external reference.
- ERD has no bridge-message storage column; `bridge_messages_json` added so the
  optional `messages` endpoint (API §15) has somewhere to persist. Justified by
  the API spec's bridge-message feature.
- Endpoints are scoped to the **user** chatbot (`/api/users/chat/handoffs`) since
  human escalation is a renter/host support path; the API spec leaves the surface
  un-scoped.
- Actual case/ticket routing to a support team (REQUIRED→ROUTED→ACCEPTED and
  `support_case_ref` population) requires an external support/case system that
  does not exist in this repo; the wiring and status contract are in place and
  `support_case_ref` is only ever set externally.

## Phase 5 — Knowledge Base + RAG — ✅

Scope confirmed with the user: **Level-1 full-text (FTS) scope**. No vector /
embedding provider; portable keyword matching used so tests (SQLite) and
production (Postgres) share one code path, and the retrieval surface is
provider-neutral so an embedding/reranker can be added later.

### What was implemented
Per `ZR-AI-KB-006` + `ZR-AI-RAG-001` (Level-1 subset):

1. **KB data model.** New `backend/app/models/kb.py` — `KbRelease`
   (DRAFT→ACTIVE→REVOKED, market-scoped), `KbDocument` (slug, market,
   jurisdiction, domain, access_class, trust_tier, effective/expiry date,
   status, release ref), `KbChunk` (section + content + lowercased
   `content_search` for filtering). Controlled taxonomies for markets, access
   classes, domains and statuses.
2. **Ingestion + chunking + security quarantine** (`backend/app/services/kb.py`):
   `chunk_document` preserves Markdown heading structure and keeps chunks
   bounded (~180 words); `ingest_document` runs a **security scan** against
   prompt-injection and secret markers (RAG §13) and quarantines offending
   content (status `QUARANTINED`, not retrievable). `make_active` /
   `revoke_document` control document lifecycle; quarantined docs cannot be
   activated.
3. **Release-governed RAG retrieval** (`backend/app/services/rag.py`): `retrieve`
   filters by ACTIVE release + document ACTIVE + market (GLOBAL/ENGLAND) +
   domain + access class + effective/expiry window (RAG §5). Returns ranked
   `RetrievalHit`s each with a `Citation` object (source/version/section/chunk/
   release/market/effective_at).
4. **Citation validator (RAG-FR-012).** `resolve_citation` rejects fabricated or
   unresolved identifiers, and re-checks current eligibility (revoked/expired →
   unresolved) so a source never survives revocation.
5. **Chat tool** `search_knowledge` (user tool, RBAC-gated via the PDP): returns
   citational evidence from `retrieve`; the user system prompt now instructs the
   model to prefer cited sources and never treat retrieved content as live
   transaction truth.
6. **Admin KB routes** (`backend/app/api/routes/knowledge.py`, prefix
   `/api/admin/knowledge`): create document (ingest+chunk+quarantine), list
   documents, get chunks, activate document; super-admin-only create/activate
   release and revoke document; taxonomies endpoint. Every governance step is
   audited.

### Files touched
- `backend/app/models/kb.py` — new models + taxonomies.
- `backend/app/services/kb.py` — new (chunking, ingestion, quarantine, lifecycle).
- `backend/app/services/rag.py` — new (retrieval, citations, validator).
- `backend/app/services/chat_service.py` — `search_knowledge` tool + prompt note.
- `backend/app/api/routes/knowledge.py` — new admin KB routes.
- `backend/app/main.py` — register `knowledge.router`.
- `backend/alembic/versions/0021_knowledge_base.py` — new migration (head `0021_knowledge_base`).
- `src/lib/user-chat.ts`, `src/components/user/chat/UserChatPanel.tsx` —
  `handoffSuggested` on done + "Contact a human" affordance.
- `backend/tests/test_kb.py` — new (26 tests).

### Tests added (`test_kb.py`, 26)
- Chunking: headings preserved, bounded chunks, empty input.
- Ingestion: normal→DRAFT, injection/secret→QUARANTINED, quarantined cannot
  activate, invalid market rejected.
- Retrieval: only ACTIVE-release docs, revoked excluded, market/access/domain
  filters, expired/not-yet-effective excluded.
- Citations: resolves to eligible chunk, fabricated rejected, post-revocation
  rejected, hits_to_text includes source ref.
- Chat tool: returns evidence, no-hit info, registered for users, not for admins.
- Admin routes: create→list→chunks→activate flow, release requires super_admin,
  end-to-end publish + user retrieval.

### Result
`pytest backend/` → **169 passed** (143 + 26 new). Alembic head single
(`0021_knowledge_base`). `tsc --noEmit` and eslint clean. No regressions.

### Deviations from spec (Level-1)
- **No vector similarity / embeddings** — deferred (provider-neutral surface
  kept open). Retrieval uses portable keyword matching (ILIKE-aware scoring)
  that is deterministic and works on SQLite + Postgres.
- Access classes beyond `K0_PUBLIC` are modelled and filtered but only surfaced
  to staff contexts later; no staff-facing chatbot consumer yet.
- Locality overlays beyond a single `market` string are not implemented.
- Real maker-checker (separate reviewer vs approver), immutable release manifest
  snapshotting, and external ingestion/approval pipelines are future work; the
  endpoints model the create→approve→release→revoke flow available today.
- Postgres `tsvector`/GIN optimization is intentionally not emitted (portable
  ILIKE path used everywhere); a future embedding/tsvector index can be layered
  on without contract changes.

## Phase 6 — Evaluation test suite — ✅

### What was implemented
A **deterministic evaluation harness** running the golden case matrix from
ZR-AI-EVAL-001 (Level-1, no live model calls) against the real implemented
subsystems (guardrails, PDP/authorization, RAG/retrieval, handoff/privacy).
Scoped to the deterministic surface of the eval doctrine: versioned golden
datasets, per-family zero-tolerance gates, a structured release report, and a
pytest/CI hook so regressions fail the build.

1. **`backend/app/evals/cases.py`** — versioned golden cases:
   - Guardrails: risk (R0–R2), action tier (A1/A3 incl. housing-safety
     escalation), determination-assertion blocking, human-request detection.
   - Authorization: RBAC + ABAC object/state gates (cross-account, super-admin
     bypass, PUBLISHED state) mirroring AUTH-I-005 / AUTH-I-007 invariants.
   - RAG: only-active-release retrievable, unpublished not, revoked not
     (RES-011), market filter, expired excluded, citation validator rejects
     fabricated/malformed (RAG-FR-012).
   - Privacy: handoff packet never surfaces secret/prompt-injection content
     (FRS-HO-002).
   - Invariant strings are recorded in the report so each family documents the
     requirement it enforces.
2. **`backend/app/evals/runner.py`** — `run_evals()` builds its own isolated
   in-memory SQLite engine (mirrors the pytest conftest ARRAY→JSON patch) and
   runs each family; returns a `release_gate` with `blocked` / `blocking_families`
   per the binding release doctrine (zero tolerance for any failure in
   safety/privacy/authorization/transaction-integrity).
3. **`backend/app/evals/__init__.py` / `__main__.py`** — CLI `python -m app.evals`
   (optionally `--out report.json`) emits a versioned JSON report with
   `eval_version`, `generated_at`, per-family pass/fail + failures, and the
   gate decision; exit code non-zero when blocked.
4. **`backend/tests/test_eval_suite.py`** — runs the harness inside the normal
   suite; any zero-tolerance family failure or unexpected failure record fails
   CI.
5. **Guardrails fix surfaced by the harness:** `handoff_requested` failed to
   detect "connect me with a support agent" because the human-request regex did
   not allow an optional article between "with" and "support"; the regex now
   accepts `connect me with a/an support`. Verified against `test_handoff.py`.

### Files touched
- `backend/app/evals/cases.py`, `runner.py`, `__init__.py`, `__main__.py` — new.
- `backend/app/services/handoff.py` — human-request regex article fix.
- `backend/tests/test_eval_suite.py` — new.

### Tests added
- `test_eval_suite.py` (4): release gate not blocked; all families ok; report
  version/counts correct incl. the four core families; no unexpected failures.

### Result
`pytest backend/` → **173 passed** (169 prior + 4 new). No regressions.
CLI report (34/34) gate **PASS**, exit code 0.

### Deviations from spec
- Scoped to the **deterministic** surface of ZR-AI-EVAL-001 (retrieval,
  authorization, safety, privacy, fairness-escalation). Probabilistic quality
  scoring (answer correctness/groundedness/helpfulness mean-scores), human
  review calibration, latency/cost benchmarks, and full WCAG 2.2 AA automation
  are out of scope here — they require a live model/reviewer and are deferred.
- No live LLM calls are made; the harness asserts the deterministic gates that
  the prompts/guardrails/PDP/RAG rely on, matching the eval doctrine's
  "zero-tolerance deterministic gates are assessed separately from probabilistic
  quality" clause.

## Phase 7 — Remaining low-priority items — ✅

### What was implemented
Closed the remaining gaps: a server-authoritative **feature-flag** subsystem
(ZR-AI-DEVOPS / FRS §2 / Tech-Arch §22), the **audit gap** around flag changes,
and **frontend unit tests** (new JS test runner).

1. **Feature-flag subsystem** (`backend/app/services/feature_flags.py`):
   - Allow-listed registry of `FlagSpec` (only known names exist; unknown names
     raise `FeatureFlagError`).
   - Safe defaults: core capabilities `assistant.stream`, `assistant.handoff`,
     `assistant.rag.search_knowledge` default ON; `assistant.england_launch`
     (England market-pack guidance) defaults OFF until enabled per market.
   - Market/role scoping: `england_launch` is only effective within `ENGLAND` —
     a GLOBAL/no-market query never activates it (no jurisdiction leakage).
   - Kill-switch semantics: a DB override can kill a whole family; enforced in
     `execute_tool` for any tool declaring a `flag` (PDP permit still runs first,
     then the flag gate). Authorization/redaction/action-confirmation are not
     flags and can never be disabled.
   - DB overrides persisted in a new `FeatureFlag` table; every change is
     audit-logged (`feature_flag.updated`) — closes the audit gap.
2. **Tool wiring** (`backend/app/services/chat_service.py`): `ToolSpec` gains a
   `flag` field; `search_knowledge` declares `flag="assistant.rag.search_knowledge"`;
   `execute_tool` gates flagged tool families after PDP authorization.
3. **Admin API** (`backend/app/api/routes/feature_flags.py`, prefix
   `/api/admin/feature-flags`, user-auth): GET list (any admin) of every allowed
   flag with default/effective/value/description/scope + invariants; PUT update
   (super_admin only, audited, 403 for non-super, 422 unknown flag, 422 market
   guard). Registered in `main.py`. Migration `0022_feature_flags` (single head).
4. **Frontend tests** (`vitest` + `@testing-library` added as devDeps):
   - `vitest.config.mts` (jsdom, `@/` alias, jest-dom setup file).
   - `src/lib/user-chat.test.ts` — SSE parser: text/tool/done events incl.
     `handoffSuggested`, default when absent, ApiError on non-ok rejection.
   - `src/lib/risk.test.ts` — `classifyRisk`/`riskTopicName` (R0/R2/R3/R4,
     topic naming).
   - `npm test` script added; +8 tests, `tsc --noEmit` and `eslint` clean.

### Files touched
- `backend/app/services/feature_flags.py` — new (FLAG_REGISTRY, is_enabled,
  effective_flags, set_flag).
- `backend/app/models/feature_flag.py` + `models/__init__.py` — new model.
- `backend/app/api/routes/feature_flags.py`, `backend/app/main.py` — new router.
- `backend/app/services/chat_service.py` — ToolSpec.flag + execute_tool gate.
- `backend/alembic/versions/0022_feature_flags.py` — migration.
- Frontend: `vitest.config.mts`, `src/test/setup.ts`, `package.json` (test scripts,
  devDeps), `src/lib/user-chat.test.ts`, `src/lib/risk.test.ts`.

### Tests added
- `test_feature_flags.py` (19): allow-list, unknown→error, safe defaults,
  market scoping/no-leak, override flip + audit event, kill switch gates tool in
  execute_tool, admin list, super-admin update, 403/422 guards.
- Frontend `user-chat.test.ts` (3) + `risk.test.ts` (5) = 8.

### Result
`pytest backend/` → **192 passed** (173 + 19). No regressions.
`npm test` → **8 passed**; `tsc --noEmit` clean; `eslint` clean (0 errors).
Alembic single head (`0022_feature_flags`).

### Deviations from spec
- Feature flags are stored as DB overrides on top of the registry default (no
  signed/versioned config artifact or external flag service); the interface is
  deterministic and server-authoritative, matching the spec's intent while
  avoiding a new external dependency.
- Frontend tests cover pure helpers + SSE parsing (no heavy component/e2e or
  WCAG automation); kept to deterministic, low-brittleness units.
- `assistant.england_launch` is modelled and market-scoped but not yet wired to
  a specific regulated-guidance tool beyond the registry contract (deferred
  with KB market packs).

---

**All 7 phases complete.** Cumulative: `pytest backend/` → 192 passed;
frontend `npm test` → 8 passed; `tsc` + `eslint` clean; Alembic single head
(`0022_feature_flags`); eval release gate PASS (34/34); `IMPLEMENTATION_PROGRESS.md`
documents each phase with deviations.

---

# Phase 8 — Scenario-Based QA Pass (Categories A–H)

**Status: ✅ Complete — 0 defects.**

A real-behavior QA pass over the actual HTTP/SSE entrypoints (not a pytest re-run),
documented in `TEST_REPORT.md` at the repo root. Drives the real FastAPI routers
(auth, rate limiting, SSE framing, DB persistence, audit) and the real
`stream_assistant_reply` loop (guardrails, bounded tool loop, PDP, RAG grounding);
only the external Groq model provider is swapped for a deterministic fake
(`build_client` seam reused from `test_guardrails.py`). No new third-party deps.

### New artifacts
- `backend/tests/test_qa_scenarios.py` — 44 scenario tests across A/B/C/D/E/F/H
  (real routes + fake-Groq seam).
- `src/lib/risk-agreement.test.ts` — cross-stack risk spot-check (frontend
  `classifyRisk` vs server `classify_risk`) for R0/R2/R3 + documented R3 boundary
  divergence on "application status" (server authoritative; client is a
  ZR-AI-UX-001 §6 Layer-C indicator only).
- Re-ran `python -m app.evals` (Category G, no change): release gate PASS 34/34.

### Results (all PASS)
- **A Guardrails** 8/8 — A1/A2/A3 tiers in real SSE `done`; determination blocked +
  `user_chat.guardrail.determination_blocked` audit row; R0/R2/R3 surfaced == server
  classifier.
- **B RBAC/ABAC (PDP)** 8/8 — `AUTH_SCOPE_MISMATCH`, `AUTH_OBJECT_RELATIONSHIP_MISSING`,
  `AUTH_PROPERTY_DENIED`, `AUTH_RESOURCE_NOT_FOUND`; ownership + PUBLISHED-only checks;
  super_admin scope pass.
- **C RAG/KB** 8/8 — ACTIVE-only retrieval, revoke, market/domain/expiry filters,
  citation resolve + fabricated-id reject, injection quarantine (refuses to activate,
  never retrievable), tool role-scoping.
- **D Handoff** 6/6 — `handoffSuggested` on done; create `supportCaseRef` null
  (FRS-HO-004); 409 duplicate; cross-user 404; packet strips secrets; cancel 200/409.
- **E Feature flags** 5/5 — unknown 422; non-super 403; super flip + `feature_flag.updated`
  audit; England market no-GLOBAL-leak; kill switch gates tool after PDP.
- **F Security** 6/6 — prod-boot refusal; chat rate-limit 429 + reset; forced internal
  error leaks nothing sensitive; tampered JWT 401; admin-token-on-user 401; secure-cookie
  prod config boots.
- **G Eval harness** — `python -m app.evals`: 34/34, gate not blocked.
- **H Regression/cross-cutting** 3/3 — tool-call conversation lifecycle persisted
  (`tool_calls_json`, SSE `tool` event, `messageId` round-trip); no tool replay on 2nd
  turn; delete cascade + audit.

### Full regression (final)
`pytest backend/` → **236 passed** (192 existing + 44 QA).
`npm test` → **12 passed** (8 + 4 risk spot-check); `tsc --noEmit` clean; `eslint` clean.

### Deviations / notes
- Live Groq model not exercised (offline); model seam is the one substituted component.
- SQLite (ARRAY→JSON patched) harness, consistent with the rest of the suite; PG-only
  behaviours covered by migration tests.
- Recommend one real-key smoke pass pre-deployment (see `TEST_REPORT.md` §5).

---

# Bugfix — Chat tools failing on live Postgres ("Couldn't fetch that data")

**Status: ✅ Resolved.** Full write-up in `DIAGNOSTIC_my_obligations.md` at repo root.

## Symptom
Every `my_obligations` / `my_applications` / `my_occupancies` chat tool call failed with
"Couldn't fetch that data". Backend logs showed `InFailedSqlTransaction` during
`INSERT INTO chat_messages` (`user_chat.py:167`) then `PendingRollbackError` in `audit()`
(`user_chat.py:213`).

## Root cause (DB-level, Postgres-only)
The live DB at `localhost:5433/zoiko_rooms` was **stale at alembic `71f4a2b9c0aa`** and the
migration chain had **two heads**: `0022_feature_flags` and `d3f1a9c2b6e4_link_guests_to_user_accounts`.
The un-applied `d3f1a9c2b6e4` adds `guests.user_account_id`, which
`get_guest_for_user` (guest.py:19) and every `my_*` tool query against Postgres require.
`information_schema` confirmed the column (and any `user_account_id` column anywhere) did not
exist → the tool's first SELECT aborted the transaction → cascaded `InFailedSqlTransaction`
+ `PendingRollbackError`. The SQLite test harness hides this because it builds tables straight
from ORM metadata (the column always existed in tests) — hence it was Postgres-only.

## Fix
- NEW empty merge migration `backend/alembic/versions/e49ffda0d565_merge_feature_flags_and_guest_account_.py`
  (`down_revision = ('0022_feature_flags', 'd3f1a9c2b6e4')`) → single head `e49ffda0d565`.
- Ran `python -m alembic upgrade head` on the live DB, applying `d3f1a9c2b6e4` (adds
  `guests.user_account_id`), `0020_ai_handoff`, `0021_knowledge_base`, `0022_feature_flags`.
- Verified: `alembic_version = e49ffda0d565`; `guests.user_account_id` exists; all `my_*`
  handlers run cleanly against live DB for every user (no transaction errors).

## Regression
`pytest tests/` (from `backend/`) → **288 passed**. Tool handlers exercised vs live Postgres —
no errors.

## Recommendation
Add a pre-deploy gate asserting `alembic current == alembic heads`; consider logging/auditing
the real exception on `tool_error` (currently the broad `execute_tool` catch-all at
`chat_service.py:695` masks it and `tool_error` never logs server-side).
