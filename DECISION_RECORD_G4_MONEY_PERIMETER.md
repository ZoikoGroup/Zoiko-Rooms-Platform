# Decision Record: G4 Money Perimeter Conflict

**Date**: 2026-09-21
**Register**: ZR-WIR-TRACE-001 (Final Wireframe Implementation & Traceability Register) — Gate G4
**Status**: Open — requires Executive/Product decision + Legal/Compliance + Architecture sign-off
**Raised by**: Engineering, during a ZR-PAY-002 Payments-domain traceability audit against ZR-WIR-TRACE-001

---

## 1. The conflict

ZR-WIR-TRACE-001 Section 10 defines Gate G4 as a required release gate:

> **G4 — Money perimeter — PASS required.** *Release condition: Listing Fee is the only Zoiko-collected payment; no rental-fund custody paths exist.*

The same document restates this at Section 3 ("Money"), Section 5.2 ("Financial perimeter"), and in the ZR-PAY-002 binding rule (Section 5): *"No rent/deposit checkout into Zoiko accounts; no wallet/escrow/payout balance model."*

**This gate does not currently pass.** `backend/app/crud/payment_provider.py` and `backend/app/crud/finance.py` — a pipeline built under an earlier specification (ZR-ENG-CLR-005), predating ZR-PAY-002 and this register — dispatch real Stripe API calls (`stripe.PaymentIntent`, Stripe Connect transfers/payouts) against `SimulatedPayment`/`Obligation`/`PayoutRecord` rows that represent **rent and deposit** money, not the Listing Fee. This is a second, independent real-money path alongside the new, spec-compliant Listing Fee domain (`crud/listing_fee.py`), which itself is fully isolated and does pass G4 on its own.

## 2. What was checked

- `TestA1RentalPaymentDomainNeverTouchesAPaymentProvider` (`backend/tests/test_zr_pay_002_acceptance_gates.py`) confirms the **new** ZR-PAY-002 rental-payment-record domain (`crud/rental_payment.py`) never imports or calls Stripe — that module is clean.
- The conflict is entirely in the **pre-existing** `crud/payment_provider.py` / `crud/finance.py` pipeline, which was not built by, and predates, this Payments-domain implementation effort.
- Live-checked: `crud/payment_provider.py:dispatch_payment_to_provider` calls the real Stripe API whenever `settings.stripe_secret_key` is configured (which it now is, for the Listing Fee integration) — so this legacy path is not dormant; it is live and reachable in this environment today.

## 3. Why this isn't an engineering fix

ZR-WIR-TRACE-001 Section 11 ("Change Control After Wireframe Freeze") classifies exactly this kind of change:

| Change class | Definition | Approval / artifact requirement |
|---|---|---|
| **Money movement** | New payee, custody, wallet, escrow, settlement, payout, deposit handling. | **Executive/product decision + Legal/Compliance + Architecture; new wireframe/spec required.** |

Removing or disabling the legacy pipeline is a "money movement" change in the other direction (removing an existing custody path affects whatever process or commercial commitment currently relies on it — host payouts, existing simulated-payment tests/tooling, possibly production data). It is explicitly **not** a "local engineering decision" per Section 2.1's conflict rule. Engineering has not made this change unilaterally.

## 4. Options for the decision owner

1. **Retire the legacy pipeline.** Formally deprecate `crud/payment_provider.py`/`crud/finance.py`'s real-money paths for rent/deposits, migrating any still-needed capability (e.g. host payouts for the Listing Fee itself, if any) into the ZR-PAY-002-compliant domain. This is the only path that makes G4 literally true.
2. **Formally re-scope G4.** If the legacy pipeline is serving an approved, separate business purpose outside ZR-PAY-002's boundary (e.g. a distinct, already-approved custody product), document that explicitly as an approved deviation rather than leaving G4 in an ambiguous state.
3. **Leave both pipelines coexisting under an explicit, time-bound exception**, with a committed retirement date — acceptable only if Architecture/Compliance formally sign off per Section 10's release rule: *"A screen or service does not ship with an 'N/A' against a mandatory gate unless Architecture, Product, Security/Compliance ... and QA have documented why the gate is genuinely non-applicable."*

No option has been selected. This record exists so G4's status is never silently reported as passing.

## 5. Traceability block (per Appendix A)

```
Wireframe / workflow:        ZR-WIR-TRACE-001 Section 10, Gate G4
Traceability ID(s):          G4
Frontend route / component:  N/A (backend money-flow architecture)
Authoritative service/object: crud/payment_provider.py, crud/finance.py (legacy);
                              crud/listing_fee.py (compliant, unaffected)
API / projection used:       N/A
Allowed roles / relationship rule: N/A
Jurisdiction / market-pack rule:   N/A
State transitions covered:   N/A
Evidence / audit record:     This file; TestA1RentalPaymentDomainNeverTouchesAPaymentProvider
Canonical event(s):          N/A
Security / privacy controls: N/A
Accessibility evidence:      N/A
Observability / error handling: N/A
QA acceptance test IDs:      A1 (partial pass -- scoped to the new domain only, see Section 2 above)
Approved deviation (if any): NONE -- pending decision, see Section 4
```
