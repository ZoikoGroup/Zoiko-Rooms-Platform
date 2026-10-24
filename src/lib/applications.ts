import { UserApplication } from "@/lib/types";

const CLOSED_OFFER_STATUSES = new Set(["DECLINED", "EXPIRED", "WITHDRAWN"]);
const CLOSED_AGREEMENT_STATUSES = new Set(["VOID", "EXPIRED"]);

/** Whether this application still blocks applying again for the same room:
 *  true while it's in progress or became a tenancy; false once it ended --
 *  withdrawn, rejected by the host, its offer declined/expired/withdrawn, or
 *  its agreement voided/expired (ZR-37: a renter who declined an offer could
 *  never apply again). */
export function isApplicationOpen(app: UserApplication): boolean {
  if (app.status === "WITHDRAWN") return false;
  if (app.agreementStatus) return !CLOSED_AGREEMENT_STATUSES.has(app.agreementStatus);
  if (app.offerStatus && CLOSED_OFFER_STATUSES.has(app.offerStatus)) return false;
  if (app.decision === "REJECTED") return false;
  return true;
}

/** Listing ids the renter has an open application for. */
export function listingsWithOpenApplication(apps: UserApplication[]): Set<string> {
  return new Set(apps.filter(isApplicationOpen).map((a) => String(a.listingId)));
}
