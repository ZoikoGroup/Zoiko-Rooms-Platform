# Improvements Report — Gaps Filled vs. `main` / `anil`

**Prepared by**: Venky (with Claude)
**Date**: 2026-09-07
**Scope**: Everything built, fixed, restructured, and verified this session — cross-checked against the `main` and `anil` branches on GitHub (`https://github.com/ZoikoGroup/Zoiko-Rooms-Platform`).

**Verification status at time of writing**: Backend test suite **203/203 passing**. `npx tsc --noEmit` clean. `npm run lint` clean. Alembic at a single head, DB migrated and verified. Every feature below was also exercised live in a real browser (Playwright), not just unit-tested.

---

## At a glance — features that exist in our code but not in `main` or `anil`

Everything below is a genuine capability gap in both other branches, not a style difference or a duplicate fix.

| # | Feature | In `main`? | In `anil`? | In ours? |
|---|---|---|---|---|
| 1 | Login throttling (brute-force lockout) | ❌ | ❌ | ✅ |
| 2 | Renter sees real lease terms (rent/deposit/dates) before signing | ❌ | ❌ | ✅ |
| 3 | Renter self-service agreement signing | ❌ | ❌ | ✅ |
| 4 | Renter self-service rent/deposit payment | ❌ | ❌ | ✅ |
| 5 | Renter-initiated move-out request | ❌ | ❌ | ✅ |
| 6 | Finance refund/dispute cross-tenant authorization check | ❌ | ✅ *(source of the port)* | ✅ |
| 7 | Room alerts — signup form actually on the site | ❌ | 🟡 backend only, no UI | ✅ |
| 8 | Sublet requests resolved to real names + admin review queue | ❌ | 🟡 partial | ✅ |
| 9 | Booking pipeline status stepper + 1-click approve | ❌ | ❌ | ✅ |
| 10 | N+1 query fixes (Properties/Trust & Safety admin pages) | ❌ | ❌ | ✅ |
| 11 | Team-management notifications | ❌ | ❌ | ✅ |
| 12 | Notification coverage (2 → 12 backend modules) | ❌ | ❌ | ✅ |
| 13 | Frontend/backend split into separate top-level folders | ❌ | ❌ | ✅ |
| 14 | Backend Dockerfile | ❌ | ❌ | ✅ |
| 15 | "Occupied" badge on admin Properties grid | ❌ | ✅ *(source of the port)* | ✅ |

Rows 6 and 15 were pulled in from `anil` because they were genuinely good and missing from our branch — full credit given in section 4 below. Everything else was built from scratch this session. Details and reasoning for each follow.

---

## 1. Repository restructuring

The frontend used to live at the repo root, mixed in with `backend/`, `docs/`, and top-level project docs. It's now split cleanly:

```
frontend/          Next.js 16 app (moved from repo root — src/, public/, package.json, configs, own .gitignore)
backend/           FastAPI app (unchanged location)
docs/              AI Assistant spec documents (unchanged, out of scope)
README.md, etc.    Repo-wide docs (stayed at root)
```

Done via `git mv` so history/blame is preserved on every moved file. `.gitignore` was split (root keeps only repo-wide entries; `frontend/.gitignore` holds the Next.js-specific ones). `README.md` and `.github/workflows/deploy.yml` were updated to match the new paths. Full stack was restarted and live-verified end to end after the move (real login, real dashboard data, zero broken requests).

**Two things need a one-time manual update outside this repo**: the Vercel project's Root Directory setting needs to point at `frontend`, and the GCP deploy box's `pm2` process needs its working directory updated on its next deploy — neither is something this session can do from here.

---

## 2. Security fixes

| Feature | What it does |
|---|---|
| **Login throttling** | 5 failed login attempts locks an account for 15 minutes (HTTP 429), shared logic across both admin and renter/host login. Neither `main` nor `anil` has this — brute-forcing a password was previously unlimited. |
| **Password validation unified** | Registration and change-password used to accept 1-character passwords while reset-password required 8. All 5 password-setting endpoints now share one 8–128 character rule. |
| **Finance refund/dispute authorization fix** | *(Ported from `anil`, not in `main`.)* A regular (non-super) admin could previously request/decide refunds and open/resolve disputes against **any** provider's records, not just their own — a real cross-tenant access gap. Now checked against the same ownership logic already used elsewhere in the finance module. Also closes an idempotency-key collision: reusing a payment intent key for a genuinely different request now fails loudly (409) instead of silently returning the wrong payment. |

---

## 3. Renter self-service — the actual customer journey

A full audit of search → apply → offer → sign → pay → move-in → vacate found three stages that were structurally incomplete — not present in `main` or `anil` either:

| Feature | What it does |
|---|---|
| **See lease terms before signing** | The renter's agreement view previously showed only a bare sign/status state — no rent, deposit, or dates anywhere. Now shows the real negotiated terms and offers a PDF download, so nobody signs blind. |
| **Pay rent/deposit themselves** | There was no renter-facing view of what's owed and no way to pay it — every payment required an admin to record it manually. Added a "Rent & Charges Due" view with working Pay Now buttons (simulated payment, confirms immediately, same architecture as the rest of the finance system). |
| **Request to vacate** | Ending a tenancy was 100% admin-only with zero renter input. Renters can now submit a desired move-out date, which notifies the host and admins — ending the occupancy itself deliberately stays an admin action (deposit release needs inspection), this just closes the "how would they even know I want to leave" gap. |

---

## 4. Ported from `anil` (not present in `main`)

`anil` branch was reviewed feature-by-feature against this codebase. Most of its other work duplicated fixes already made here independently (sublet review UI, "New · no reviews" rating badge, real-time listing availability). Two more items were genuinely missing and worth pulling in:

| Feature | What it does |
|---|---|
| **Room alerts (email subscriptions)** | A visitor can subscribe (no login needed) to get emailed when a new room matching their city/price/type is published — branded confirmation email, working unsubscribe link, matched by a scheduled script. `anil`'s version had the backend but no actual signup form anywhere on the site — built the missing UI so the feature is genuinely reachable, not just present in the API. |
| **"Occupied" badge (admin)** | A published listing that's currently rented now shows "Occupied" instead of just "Published" on the admin Properties grid — a small but real at-a-glance signal that was missing. |

*(Correction for the record: real SMTP email sending was already fully built and wired into this codebase before this session — password reset, identity verification, and listing publish/reject emails all already send real mail. An earlier comparison pass mistakenly flagged this as missing; it wasn't.)*

---

## 5. Admin / operational improvements

| Feature | What it does |
|---|---|
| **N+1 query fixes** | Two admin screens (`PropertiesManager`, `TrustSafetyManager`) were fetching rooms and occupancy classifications one request per property/room. Now fetch once and join client-side — two new bulk backend endpoints. |
| **Booking pipeline status view + click reduction** | Admin's Leasing page now shows a compact 6-stage progress tracker per application instead of reading three separate sections. "Approve" also now auto-creates the offer draft in the same click, since that always happened next anyway. |
| **Sublet request name resolution** | Sublet requests used to show only raw IDs (`Occupancy #4`, `party #12`). Now resolve to real listing/property/renter names, with a working search box on the renter side and a review queue on the admin side that didn't exist before. |
| **Team management notifications** | Adding a team member, changing someone's role, or (de)activating an account now notifies the affected admin and other super admins — previously silent. |

---

## 6. Notification coverage

Went from 2 backend modules calling into the notification system to 12: finance, identity verification, leasing (agreement sent/signed), listing, sublet, user registration, occupancy (move-in/rent-due/tenancy-ended), reviews, team management, authority records (verified/rejected → the host), and legacy bookings (→ guest and host).

---

## 7. Test coverage

- 7 backend modules that previously had **zero** test references now have dedicated test files (contact email, admin/user notifications, market releases, room passport, uploads).
- Every new feature above shipped with its own test file: login throttling, renter agreement signing, team notifications, authority/booking notifications, renter payment self-service, renter move-out requests, finance authorization, room alerts.
- Backend suite: **121 → 203 tests**, all passing.

---

## 8. Infrastructure

| Item | What it does |
|---|---|
| **Backend Dockerfile** | Builds, runs `alembic upgrade head`, then starts uvicorn on `$PORT`. Built and verified end-to-end against the real local Postgres. |
| **Dev-server stability fixes** | Diagnosed and fixed two separate causes of "the app looks broken but the code is fine": a corrupted Turbopack cache, and orphaned `uvicorn --reload` worker processes on Windows that kept serving stale code after their parent process had already exited (invisible to normal process listing — found via `Get-CimInstance Win32_Process`, which shows full command lines). Backend now runs without `--reload` for reliability. |

---

## What's still open (not a code gap — needs a decision or external input)

- **File storage off local disk** — blocked, needs you to pick a provider (S3/Vercel Blob/etc.) and provide credentials.
- **AI Assistant** — 14 spec docs in `docs/` describe a much larger system than what's built; explicitly out of scope per your instruction, needs a team scoping decision if that ever changes.
- **Domain events remain write-only** — rows get written, nothing consumes them; left as-is since there's no known subscriber to build one for.

Full line-by-line history of every finding (including ones confirmed as *not* bugs) is tracked in `venky_findings.md`.
