# Venky's Findings — Dashboard Review

**Author**: Venky
**Date**: 2026-09-02
**Scope**: Manual walkthrough of the running Zoiko Rooms admin/host dashboard. These are UX and functional gaps observed while clicking through the live app — not yet cross-checked line-by-line against the codebase.

---

## 1. Rooms Available page lacks detail — ✅ FIXED
No proper detailed explanation shown on the Rooms Available page. Data should be pulled from a single "golden" source table so every live room shows the same complete, consistent set of details — no partial or inconsistent info between rooms.
**Resolution**: Added rating/reviews, bedroom count, and amenity badges to `RentBrowser.tsx`. Live-verified with a real browser session.

## 2. Room type search/filter is broken — ✅ NOT A BUG (verified)
Clicking the room-type filter/container button doesn't fetch or return results. Search is not working on this page.
**Resolution**: Tested directly against the live API with multiple casings (`Private room`, `Private Room`, `private`) — filter works correctly in every case. Could not reproduce. No fix needed.

## 3. Duplicate functions across the dashboard — ⬜ UNCONFIRMED
The same functionality appears to be implemented more than once — both in the main sidebar navigation and again inside individual pages. Needs consolidation so there's one source of truth per function instead of repeated logic.
**Status**: Swept the sidebar (`Sidebar.tsx`) — it's purely navigational, no duplicated actions found there. Need a concrete example (which function, which two places) to investigate further.

## 4. Payments tab is too shallow — ✅ FIXED (on the real finance system)
The Payments tab only fetches and displays a bare payment status. No real detail behind it (breakdown, history, context) — needs proper depth, not just a status flag.
**Resolution**: Removed fabricated trend percentages from the legacy admin Payments page (that page's underlying data model genuinely can't support property/room/tenant depth — flagged to Anil). The real depth landed on the **Finance** system instead: `SimulatedPaymentRead` now carries `guestName`/`listingName`/`propertyAddress`; wired into the Finance page's payment/refund/dispute pickers and the renter's own Payments history table (new "Room" column).

## 5. Document verification isn't linked to the next step — 🟡 MOSTLY WORKING
There's no real connection between identity/document verification and what happens after. If a document is verified as true, the process should automatically move forward to the next step — this isn't implemented as an actual working flow yet, just disconnected pieces.
**Status**: The main dashboard flow actually does enforce this correctly (backend 403s unverified users, frontend disables Apply until verified). One real gap remains: `POST /api/public/applications` bypasses the check entirely — but that's a deliberate no-auth integration point for a separate external site, not a bug in our dashboard.

## 6. Can't view/download documents from the dashboard — ✅ NOT A BUG (verified)
Uploaded documents should be viewable and directly downloadable from within the dashboard. Currently not available.
**Resolution**: Checked — a real "View document" link with a secure, access-controlled download endpoint already exists for identity verification documents, now with dedicated test coverage (`test_document_access.py`: owner access, cross-user denial, missing-file handling). Works as intended.

## 7. Ratings are mock data — ✅ FIXED
Ratings shown are hardcoded/mock, not real. Should be calculated from actual user reviews — an average across all customers who stayed in that specific room or villa.
**Resolution**: A real `POST /reviews` endpoint now exists, gated on having actually stayed at the listing (via Occupancy), and `_recompute_listing_rating()` recalculates `Listing.rating`/`review_count` as a true average on every review.

## 8. Sublet tab needs more filters — ✅ FIXED (partially — search still blocked)
The Sublet tab needs additional filters and requirement fields — current version is too bare to be useful.
**Resolution**: Added status filter tabs (All/Pending Verification/Pending Admin Review/Approved/Rejected) and fixed a real bug where a failed API load silently showed the "no requests" empty state instead of an error. **Still blocked**: search by renter/property name — `SubletRequestRead` still only returns raw IDs, no resolved names. Flagged to Anil.

## 9. Hosting flow doesn't auto-fetch location details — ✅ FIXED
When a host creates a room listing tied to a real/true location, the system should automatically fetch and pre-fill relevant details for that location — reducing manual data entry and hesitation for the host, instead of making them type everything themselves.
**Resolution**: `ListARoomWizard.tsx` now auto-fills the listing's area/neighbourhood from the property's address. Separately, the backend now also canonicalizes listing city/location from the Property record server-side, closing this from both ends.

## 10. Newly hosted rooms don't appear in Live Rooms — ✅ NOT A BUG (already handled)
After a host creates a room, it doesn't show up in the Live Rooms tab. The room exists but isn't visible where it should be.
**Resolution**: There's no literal "Live Rooms" tab — new listings correctly start at `DRAFT` and need "Submit for Review" + admin approval before going live. Checked `HostingListingsManager.tsx`: it already shows a clear state badge, an "Awaiting review by a Zoiko admin" banner, and rejection-reason + resubmit guidance. Nothing missing here.

---

## Additional Gaps (found via code & docs review, not dashboard clicking)

These were found by actually reading the backend code, migrations, and the `docs/` spec files during setup — not from the UI. Included here since a few directly explain findings above.

## 11. Published listings don't reflect real occupancy — ✅ FIXED (by the team, not this session)
A room stays visible in search as "available" (`state = PUBLISHED`) even after a renter has actually moved in (`Occupancy.status = ACTIVE`). Nothing in the code automatically pauses or unpublishes a listing when it becomes occupied — only an admin manually pausing it would hide it. This is likely connected to finding #1 (no reliable "golden table" view of true room status) and possibly #10.
`backend/app/crud/listing.py`, `backend/app/models/occupancy.py`
**Resolution**: This was the biggest architectural risk flagged all session. Now fully fixed with dedicated test coverage (`test_availability.py`): occupied rooms disappear from public search/detail, admin and host listing views show `available: false`, applications are rejected against occupied listings, pending-move-in occupancy already blocks availability, and ended occupancy correctly makes a room available again.

## 12. New host-created rooms may be stuck pre-publish — ✅ FIXED (already handled)
Related to finding #10: a new listing starts at `DRAFT` and only becomes visible after passing `REVIEW → APPROVED → PUBLISHED`. If that approval step isn't surfaced anywhere in the host's UI, a host has no way to know their room is sitting unpublished, waiting on an admin action they can't see.
`backend/app/models/listing.py` (`LISTING_STATES`)
**Resolution**: See #10 — the host UI already surfaces this clearly.

## 13. No renter/host account exists by default — ⬜ NOT FIXED (not really a bug)
`backend/seed.py` only creates an admin account and sample guest records — it never creates a real `UserAccount` (the renter/host login). Out of the box there's no way to test the renter/host side of the dashboard without registering a new account manually first.
`backend/seed.py`, `backend/app/models/user_account.py`
**Status**: Left as-is — this is a seed-data convenience choice, not a functional bug. Registration works fine; a test account was created manually for this session's testing (`renter@zoikorooms.com`).

## 14. Branding endpoint returns 401 on public/login pages — ✅ FIXED
`Logo.tsx` calls `/api/settings/branding` on every page load, including the login screens, before any authentication exists — so it 401s repeatedly in the browser console on pages that should be fully public. Cosmetically harmless (falls back to no logo) but is a real bug in how that endpoint is gated.
`src/components/ui/Logo.tsx`
**Resolution**: Branding is actually per-admin-user data, so there's no sensible "public" branding to serve on pages with no admin session anyway. Added a `fetchBranding` prop (default off) — only the admin Sidebar and Settings page (genuine admin sessions) opt in. Live-verified: zero branding calls on `/login` and `/account/login` now; admin pages still fetch it successfully (200, not 401).

## 15. Chatbot's audit log doesn't record what data it exposed — ✅ FIXED
`tool_results_json` exists as a column on `ChatMessage` but is never populated anywhere in the code. The audit trail shows which tools the AI called, but not what data those tools actually returned — a real gap if this is ever reviewed for a compliance/data-leak investigation.
`backend/app/services/chat_service.py`, `backend/app/models/chat.py`
**Resolution**: `stream_assistant_reply` now yields a `tool_result` event with a row count and up to 5 row ids per tool call (never full row content, to avoid duplicating PII into the audit log itself). Both `chatbot.py` (admin) and `user_chat.py` (user) now persist this into `tool_results_json`. Backend test suite still 117/117 passing.

## 16. AI Assistant is far behind its own spec — ⬜ NOT FIXED (strategic decision, not a bug)
The 14 design documents in `docs/` describe a full RAG-based AI assistant — vector search with citations, a knowledge base, jurisdiction-aware answers, a confirm-before-acting flow for anything beyond read-only lookups. What's actually built is a simpler tool-calling chatbot with no vector database, no citations, and no knowledge base at all. Roughly 9% of the documented spec exists in code today.
**Status**: Left untouched — this needs a real scoping conversation with the team (commit to building the full spec, or formally scale the docs down), not a code fix in a single pass.

## 17. Code references compliance documents that don't exist — ✅ RESOLVED (verified moot, re-scoped)
Re-checked: `ZR-COM-DATA-RIGHTS-001`, `ZR-INV-SEARCH-001`, `ZR-API-INT-001` no longer appear anywhere in the codebase or in the `docs/` specs — a full repo-wide search turns up nothing. The one identifier that *does* still exist (`ZR-AI-PG-001`) is a real, findable citation in `backend/app/services/chat_service.py` — which is AI Assistant code, i.e. out of scope by the same "skip AI Assistant" instruction as #16. Nothing left to fix here outside that scope.

---

## Found via a full live end-to-end booking test (search → post room → approve → apply → sign → pay → move in)

Walked one room through the entire real pipeline in a live browser, as two separate real accounts, to answer "does this actually work end to end." Found and fixed 7 more real issues along the way.

## 18. Market release eligibility check used a stale, one-time value — ✅ FIXED
`listing.market_release_id` is only ever set once, at listing-creation time. A listing created before its jurisdiction's Market Release existed (e.g. right after wiping seed data) was permanently stuck failing "Create Agreement," even after an admin activated the Market Release afterward — activating it never went back and relinked already-existing listings.
`backend/app/crud/eligibility.py`, `backend/app/crud/listing.py`
**Resolution**: Both `check_agreement_eligibility`/`check_move_in_eligibility` and the informational `check_publish_eligibility` now fall back to a live lookup by the room's jurisdiction when the stored value is stale or missing. This specific listing was also manually relinked to unblock it.

## 19. Tenant couldn't view their own occupied room — ✅ FIXED
`GET /api/public/listings/{id}` correctly hides occupied rooms from *search* (someone else can't rent a room that's taken) — but the same endpoint also backs "My Applications → view room," so once a tenant's own booking went active, clicking into their own room showed "Listing not found."
`backend/app/api/routes/public.py`
**Resolution**: The endpoint now checks whether the requesting user has ever applied to that specific listing (any status) and bypasses the availability gate for them specifically. Search results for everyone else are unaffected.

## 20. Application status showed identical "DECIDED" for approved and rejected — ✅ FIXED
The customer-facing application status only ever showed the raw top-level status (`SUBMITTED`/`WITHDRAWN`/`DECIDED`) — the real outcome (approved vs. rejected) lived in a separate `decisions` record never exposed to the user endpoint. A rejected applicant and an approved one saw the exact same badge.
`backend/app/schemas/leasing.py`, `backend/app/api/routes/user_rentals.py`, `src/components/user/ApplicationsManager.tsx`
**Resolution**: Added a `decision` field to the user-facing application response, and the frontend now shows "Approved" / "Not Approved" / "Pending Review" — mirrors the label logic the admin dashboard already used internally. Approved applications now also link directly to "My Rentals" for the real booking status.

## 21. "My Rentals" showed raw listing/room IDs instead of names — ✅ FIXED
The one screen that actually shows "yes, you're booked" (`ACTIVE` status + move-in date) displayed the room only as its raw ID (`L-DAE58324`, `Room #7`) — the backend already had the real listing name, property address, and host name available, just wasn't sent to the frontend.
`src/lib/types.ts`, `src/components/user/RentalsManager.tsx`, `src/lib/status.ts`
**Resolution**: Wired through `listingName`/`propertyAddress`/`propertyCity`/`hostName`, and relabeled the status badge from raw `ACTIVE` to "Booked & Active" (and `PENDING_MOVE_IN` → "Confirmed — Awaiting Move-In").

## 22. Fake default rating shown even with zero reviews — ✅ FIXED (expands #7)
Every new listing defaulted to a hardcoded `4.5` rating, shown as 5 near-full stars, regardless of having zero real reviews — doesn't match how any mainstream rental platform handles this (Airbnb etc. show "New," not an invented number).
`backend/app/models/listing.py`, `backend/app/crud/listing.py`, `backend/app/crud/review.py`, `src/components/ui/StarRating.tsx` (+5 usage sites)
**Resolution**: Default changed to `0.0` (never displayed as stars), `StarRating` now renders a "New" badge whenever `reviewCount === 0`, and existing zero-review listings were backfilled. Also tightened review eligibility to require the stay to have actually **ended** (was: any occupancy record at all, even pre-move-in) — matches market convention.

## 23. No way for a customer to actually submit a review — ✅ FIXED
The backend `POST /reviews` endpoint existed and worked, but nothing in the renter dashboard ever called it — no button, no form, anywhere. A real customer had zero way to leave a rating.
`src/components/user/RentalsManager.tsx`, `src/lib/user-api.ts`
**Resolution**: Added a "Leave a review" flow to "My Rentals" for ended stays — a clickable 1–5 star picker + comment box, submits to the real endpoint, handles "already reviewed" gracefully.

## 24. Self-introduced bug: a link nested inside another link — ✅ FIXED (same session)
While fixing #20, wrapped a "Check My Rentals" link inside the card's existing outer link — invalid HTML (`<a>` can't contain `<a>`), caught immediately via a real browser console error.
`src/components/user/ApplicationsManager.tsx`
**Resolution**: Moved the inner link out to be a sibling instead of a child. Confirmed zero console/page errors afterward.

---

## Remaining blockers / open gaps (not fixed — flagging for a decision)

## 25. No customer-facing way to sign a lease agreement — ✅ FIXED
Built a real renter-facing signing flow: `POST /api/users/rentals/applications/{id}/agreement/sign` (new `sign_agreement_as_renter` in `crud/leasing.py`, ownership-checked via the renter's own `guest_id`, same simulated-signature mechanics as the admin path) plus `GET .../agreement` to view it. `ApplicationsManager.tsx` now shows a "Sign Agreement" button on approved applications once the host has sent the agreement, and reflects "You signed — awaiting host" / "Agreement fully signed" states. Covered by `tests/test_renter_agreement_signing.py` (8 tests: view, sign, both-parties-flip-to-SIGNED, ownership denial, wrong-state rejection).

## 26. Booking pipeline is ~11 manual admin clicks with no single status view — 🟡 IMPROVED (both the view and the click count)
Application → Approve → Create Offer → Add Terms → Send Offer → Accept Offer → Create Agreement → Send Agreement → Sign×2 → Confirm Move-In — almost all admin-side. Added a compact pipeline stepper (`PipelineStepper` in `LeasingManager.tsx`) to each application card — Applied → Approved → Offer sent → Offer accepted → Agreement sent → Signed, at a glance, instead of reading three separate sections. Also collapsed "Approve" + "Create Offer" into a single click — approving an application always led straight to creating its offer draft anyway, with zero judgment or data entry in between, so `approve()` now chains `createOffer()` automatically. Live-verified: one Approve click now takes a fresh application straight to a DRAFT offer ready for terms. The remaining steps (terms, sending, accepting, signing, move-in) each require either real numbers a human has to enter or a deliberate go/no-go decision, so they're left as separate actions — collapsing those further would be guessing at business judgment, not removing busywork.

## 27. Authority Record room picker makes it easy to verify the wrong room — ✅ FIXED
`TrustSafetyManager.tsx` room-picker options and the Occupancy Classification list now show size + en-suite (e.g. "Room #6 — 1 Test St (120 sq ft, en-suite)") instead of just the room number.

## 28. Sublet requests still can't be searched by name — ✅ FIXED
`SubletRequestRead` now carries resolved `listingName`/`propertyAddress`/`currentRenterName`/`proposedRenterName` (new `sublet_crud.to_sublet_request_read` shared helper). `SubletRequestsList.tsx` has a real search box; the admin `OccupancyManager.tsx` sublet-review section (which didn't exist before — the approve/reject endpoints had zero frontend consumer) now shows the same resolved names.

## 29. Domain events remain write-only — 🔴 UNCHANGED (documented gap, not built)
Re-confirmed still true: `DomainEvent` rows get written but nothing anywhere consumes/processes them. Left as scaffolding — no known subscriber to build one for; speculative infrastructure would be pure guesswork.

## 30. Notification coverage still partial — 🟡 IMPROVED, NOT COMPLETE
Now 12 files call `notify_user`/`notify_admin` (was 2): finance, identity verification, leasing (agreement sent/signed), listing, sublet, user registration, occupancy (move-in confirmed, rent due, tenancy ended), review (new review → host), admin_user (team member added → new hire + other super admins; role changed / (de)activated → the affected admin), authority (verified/rejected → the self-service host who submitted it), booking (legacy admin-created flow → the guest and, if USER-hosted, the host). Still silent: guests, properties, market releases, uploads — re-checked each and none of these have a recipient distinct from the admin who's already looking at the screen where the action happened, so left alone rather than notifying someone about their own action.

## 31. N+1 query pattern in property/room and room/classification fetching — ✅ FIXED
Senior-dev audit found `properties.map(property => fetch .../rooms)` in both `PropertiesManager.tsx` and `TrustSafetyManager.tsx`, plus a second N+1 in `TrustSafetyManager.tsx` fetching `occupancy-classification` per room. Added two bulk backend endpoints (`GET /api/properties/rooms`, `GET /api/rooms/occupancy-classifications?room_ids=`) and rewrote both components to fetch once + join client-side via a `Map`.

## 32. No login throttling — ✅ FIXED
Brute-forceable admin/user login with no lockout. Added shared `core/login_throttle.py` (5 failed attempts → 15-minute lockout, HTTP 429), new columns on `AdminUser`/`UserAccount` via migration `e30b58a0360a`, wired into both `crud/admin.py::authenticate` and `crud/user.py::authenticate_user`. Covered by `tests/test_login_throttling.py`.

## 33. Inconsistent password length validation — ✅ FIXED
Registration/change-password accepted 1-character passwords while reset-password required 8. Unified via one shared `NewPassword` Pydantic type (`schemas/common.py`) used across all 5 password-setting schemas.

## 34. No Dockerfile for the backend — ✅ FIXED
Added `backend/Dockerfile` (builds, runs `alembic upgrade head` then uvicorn on `$PORT`). Built and verified end-to-end against the real local Postgres.

## 3. Duplicate functions across the dashboard — ✅ RESOLVED (re-investigated)
Went back and actually found the one real instance instead of leaving this unconfirmed: `handleLogout` was copy-pasted identically in both `Sidebar.tsx` and `Topbar.tsx` (call `logout()`, redirect to `/login`, `router.refresh()`). Everything else initially suspected (search bars, notifications, `getCurrentAdmin()` calls, branding fetch) turned out to be legitimate independent usage, not duplication. Extracted the one real case into `src/hooks/useAdminLogout.ts`, used by both. Live-verified logout still works from both the Sidebar and the Topbar profile menu.

## 35. Backend/frontend dev servers were stale mid-session — ✅ FOUND AND FIXED (infra, not code)
While live-testing the renter agreement-signing feature, every new endpoint from this session 404'd — the running backend process (port 8000) had been started before any of today's code changes and was never restarted (no `--reload`, wrong Python env). The frontend dev server (port 3000) was separately broken — a corrupted Turbopack cache (`.next/dev/cache`) throwing 500s on every route. Restarted both (backend now runs from the project's own `.venv`; cleared `.next` for the frontend) — not a code bug, but worth noting since it means *none* of this session's backend work would have been reachable in a browser without the restart. Recurred again later in the session in a nastier form: `uvicorn --reload`'s Windows multiprocessing spawn left two orphaned worker processes (still bound to port 8000, still serving stale routes) after their parent reloader had already exited and no longer showed up in `Get-Process`/`tasklist`. Root-caused via `Get-CimInstance Win32_Process` (shows full command lines, unlike `Get-Process`) and killed the actual worker PIDs. Backend is now run **without** `--reload` for the rest of this session to avoid recurrence.

## 36. Full customer-journey audit (search → apply → offer → sign → pay → move-in → vacate) — 3 real gaps found and fixed
Walked every stage of the actual renter journey end to end at the code level, not just the pipeline the admin drives. Search, apply, application tracking, and move-in were all solid. Found three genuinely incomplete features:
- **Renter never saw lease terms before/while signing** — `AgreementRead` carries no rent/deposit/dates, and there was no PDF access either. Added `GET /api/users/rentals/applications/{id}/offer` (reuses the existing `OfferRead` schema, which already nests terms + agreement) and `GET .../agreement/pdf` (reuses the existing PDF generator, ownership-checked). `ApplicationsManager.tsx` now shows the real monthly rent/deposit/term/start date and a Download PDF button.
- **No payment self-service at all** — `/api/users/payments` was read-only history; there was no obligations view and no way to pay except an admin recording it on the renter's behalf. Added `GET /api/users/payments/obligations` and `POST /api/users/payments/obligations/{id}/pay` (new `list_obligations_for_guest`/`pay_obligation_as_renter` in `crud/finance.py` — simulated like the rest of this finance system, confirms immediately). Caught and fixed a real bug in my own first pass: the obligations query only traversed via `Occupancy`, missing the initial rent+deposit that's due *before* move-in even happens (agreement-linked, no Occupancy row yet) — fixed to match the two-source pattern the admin side already uses. `PaymentsHistory.tsx` now has a "Rent & Charges Due" section with working Pay Now buttons.
- **No way to vacate** — `end_occupancy` was 100% admin-only with no renter input into scheduling it at all. Added a `requested_move_out_date`/`move_out_requested_at` pair of columns on `Occupancy` (migration `0a2f5f100b46`), a renter endpoint (`POST /api/users/rentals/occupancies/{id}/request-move-out`) that notifies the host and all super admins, and a "Request to vacate" button + modal in `RentalsManager.tsx`. Ending the occupancy itself deliberately stays an admin action (deposit inspection/release goes through Finance) — this only records intent and notifies whoever can act on it. `OccupancyManager.tsx` now shows a "Move-out requested for {date}" banner.

All three live-verified end to end in a real browser (renter paid a real pending rent obligation and watched it move into payment history; renter saw real terms and downloaded a PDF; renter requested a move-out date and the admin's Occupancy page showed the banner immediately). 23 new backend tests across 3 files, suite now 183/183.

## 37. Cross-branch review vs `anil`/`main` on GitHub — 3 real items ported in
Compared this codebase against the `anil` and `main` branches on GitHub feature-by-feature (anil = main + 2 extra commits, so a strict superset). Most of anil's work duplicated things already fixed here independently (sublet review UI, "New · no reviews" badge, listing-availability computation, real SMTP mailer infrastructure — all already present in both). Three things were genuinely missing here and got ported in:
- **Finance refund/dispute cross-tenant authorization gap** — a real security hole: a regular (non-super) admin could request/decide refunds and open/resolve disputes against *any* provider's obligations/occupancies, not just their own. Ported anil's ownership checks into `crud/finance.py` (`request_refund`, `decide_refund`, `open_dispute`, `resolve_dispute` via a new `_assert_owns_dispute_target` helper) plus an idempotency-key-collision check on `create_payment_intent`. 9 new tests (`test_finance_authz.py`).
- **Room alerts + email** — a complete public, no-login feature: visitors subscribe to be emailed when a new room matching their city/price/type is published, with a branded confirmation email, token-based unsubscribe page, and a scheduled `check_alerts.py` matcher script. New `RoomAlert` model/crud/schema, new `/find-a-room` public page, `listings.published_at` column (migration `983359abf96a`). **Note**: anil's version was backend-only with no way to actually reach it — built the missing "Create alert" signup form (`RoomAlertSignup.tsx`) so the feature is actually usable, matching this session's own audit standard of not leaving a capability stranded with no UI trigger. 11 new tests (`test_room_alerts.py`).
- **"Occupied" badge on the admin Properties grid** — a published-but-currently-occupied listing now shows "Occupied" instead of just "Published," matching the real availability computation that already existed here.

Also corrected an error in the initial cross-branch comparison table: real SMTP email sending (`core/mailer.py`, password reset / identity verification / listing publish-reject emails) was already fully wired up in this codebase before this session — the earlier claim that it was missing was wrong; the file just needed anil's two new alert-specific functions added to it.

Live-verified the ported room-alerts feature end to end: submitted a real alert through the new signup form, confirmed the DB row and a real confirmation email landed in the dev mail outbox with a working unsubscribe link; confirmed a logged-out visitor gets redirected to login when trying to view a listing or apply, exactly as intended. Backend suite now 203/203.

---

## Resolution summary
**30 of 35 tracked findings fixed or confirmed-not-a-bug across three sessions, plus 3 more substantial gaps (#36) found via a dedicated full customer-journey audit, plus 3 more items (#37) ported in from a cross-branch review against `anil`/`main` on GitHub — all fixed the same session.** Only #16 (AI Assistant scope) needs an actual team/business decision. Item #13 is a deliberate seed-data choice, not a bug. Item #29 (domain events) is a deliberately-left gap — no known consumer to build against. Item #30 (notification coverage) is meaningfully better and the remaining silent modules were re-checked and genuinely have no distinct recipient. Item #26 (booking pipeline) got both a status view and a real click reduction (Approve now auto-creates the offer draft) — what's left there is judgment/data-entry steps that shouldn't be automated away.

## Next steps
- [x] Cross-check each finding against the actual code (routes/components) to confirm root cause before fixing
- [x] Prioritize and assign
- [x] Track fixes against this list
- [x] Live-test the full booking pipeline end to end, fix what breaks
- [x] Senior-dev full-codebase audit; fix everything except AI Assistant scope
- [x] Build the renter-facing agreement signing screen (#25) — user confirmed: reuse the admin's simulated signature mechanics
- [x] Get a concrete example for #3 (duplicate actions) — found and fixed (`handleLogout` copy-paste), everything else ruled out
- [ ] Team decision needed on #16 (AI Assistant scope — confirmed all 14 docs/ specs are chatbot-only, nothing else to build from them)
- [x] #17 (compliance docs) — resolved: re-checked, the dangling citations no longer exist anywhere; the one that does is AI Assistant code
- [ ] File storage migration off local disk — blocked, waiting on the user to pick a provider (S3/Vercel Blob/etc.) and provide credentials
- [x] Test coverage added for the 7 backend modules that had zero test references: `test_contact_email.py` (admin_contact + user_contact, 10 tests), `test_notifications_routes.py` (admin_notifications + user_notifications, 7 tests), `test_market_releases.py` (4 tests), `test_room_passport.py` (4 tests), `test_uploads.py` (7 tests)
- [x] Live-tested the renter agreement-signing flow, N+1 fixes, and logout in a real browser — found and fixed a stale-dev-server issue in the process (see #35)
- [x] Added `test_admin_team_notifications.py` (4 tests) for the new team-management notifications
- [x] Added `test_authority_and_booking_notifications.py` (4 tests) for authority-record and legacy-booking notifications
- [x] Restructured the repo: frontend moved from repo root into `frontend/`, backend already in `backend/` — updated `.gitignore`, `.github/workflows/deploy.yml`, and `README.md` accordingly; live-verified the full stack still wires up correctly end to end after the move
- [x] Collapsed Approve + Create Offer into one click on the Leasing page (#26) — live-verified
- [x] Full customer-journey audit (search -> vacate); built terms visibility + PDF, payment self-service, and move-out request (#36) — live-verified
- [x] Cross-branch review against `anil`/`main` on GitHub; ported finance authz fix, room alerts + email, and the Occupied badge (#37) — live-verified
- [x] Backend suite now 203/203 passing (was 121 at the start of the senior-dev-audit pass)
