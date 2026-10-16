"use client";

import { useCallback, useEffect, useState } from "react";
import { CalendarClock, CheckCircle2 } from "lucide-react";
import { BookingChangeRequest } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { bookingChangeRequestStatusLabel, bookingChangeRequestStatusTone, bookingChangeTypeLabel } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

/** ZR-ENG-CLR-008 Section 8 -- a renter's move-in date change / extension /
 * shortening request. Read-only here: the host decides it from Rental
 * Records (approving drives the agreement amendment engine, re-signature
 * required) and Zoiko Rooms staff only see where each request stands. */
export function BookingChangeRequestsManager() {
  const [role, setRole] = useState<string | null>(null);
  const [checkingRole, setCheckingRole] = useState(true);
  const [requests, setRequests] = useState<BookingChangeRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClientFetch<BookingChangeRequest[]>("/api/leasing/booking-change-requests");
      setRequests(data);
    } catch {
      showToast("Failed to load booking change requests");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      setRole(admin?.role ?? null);
      setCheckingRole(false);
      if (admin) load();
    });
  }, [load]);

  if (checkingRole || !role) return null;

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <CalendarClock className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Booking Change Requests</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        A renter is asking to shift their move-in date, extend, or shorten their stay. The host decides; an
        approved change becomes an agreement amendment both parties re-sign. Read-only for Zoiko Rooms staff.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : requests.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-400">No booking change requests are pending review.</p>
        ) : (
          requests.map((request) => (
            <div
              key={request.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {request.listingName || `Agreement #${request.agreementId}`}
                  {" · "}
                  {bookingChangeTypeLabel[request.changeType] ?? request.changeType}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {request.guestName || "Renter"}{" "}
                  {request.changeType === "PREMISES_CHANGE" ? (
                    <>→ {request.targetListingName || request.targetListingId}</>
                  ) : request.changeType === "FINANCIAL_CHANGE" ? (
                    <>
                      → {formatCurrency(request.originalMonthlyRent ?? 0, request.currency)} → {formatCurrency(request.proposedMonthlyRent ?? 0, request.currency)}/month
                      {request.originalMonthlyRent != null && request.proposedMonthlyRent != null && (
                        <span className={request.proposedMonthlyRent > request.originalMonthlyRent ? "text-accent-700 dark:text-accent-400" : "text-emerald-600 dark:text-emerald-400"}>
                          {" "}({request.proposedMonthlyRent > request.originalMonthlyRent ? "+" : ""}
                          {formatCurrency(request.proposedMonthlyRent - request.originalMonthlyRent, request.currency)}/mo)
                        </span>
                      )}
                    </>
                  ) : request.changeType === "TERM_SHIFT" ? (
                    <>
                      · move-in {formatDate(request.originalStartDate)} → {formatDate(request.proposedStartDate)}
                      {request.proposedEndDate && (
                        <>, end date → {formatDate(request.proposedEndDate)}</>
                      )}
                      {request.additionalTermMonths != null && ` (new term: ${request.additionalTermMonths} months)`}
                    </>
                  ) : request.changeType === "DEPOSIT_CHANGE" ? (
                    <>
                      → {formatCurrency(request.originalDepositAmount ?? 0, request.currency)} → {formatCurrency(request.proposedDepositAmount ?? 0, request.currency)}
                      {" (approval is request-only -- process the actual amount via Deposit Records)"}
                    </>
                  ) : request.changeType === "LEGAL_ORDER_CHANGE" ? (
                    <>
                      {request.proposedMonthlyRent != null && <>→ rent {formatCurrency(request.proposedMonthlyRent, request.currency)}/month </>}
                      {request.additionalTermMonths != null && (
                        <>· move-in {formatDate(request.originalStartDate)} → {formatDate(request.proposedStartDate)}, new term: {request.additionalTermMonths} months </>
                      )}
                      {request.authorityEvidenceRef && <>— evidence: {request.authorityEvidenceRef}</>}
                    </>
                  ) : (
                    <>
                      · {formatDate(request.originalStartDate)} → {formatDate(request.proposedStartDate)}
                      {request.additionalTermMonths != null &&
                        ` (${request.additionalTermMonths > 0 ? "+" : ""}${request.additionalTermMonths} month${
                          Math.abs(request.additionalTermMonths) === 1 ? "" : "s"
                        })`}
                    </>
                  )}
                </p>
                {request.reason && (
                  <p className="mt-0.5 text-xs text-slate-400">“{request.reason}”</p>
                )}
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  Requested {formatDate(request.createdAt)} · expires {formatDate(request.expiresAt)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={bookingChangeRequestStatusTone[request.status] ?? "neutral"}>
                  {bookingChangeRequestStatusLabel[request.status] ?? request.status}
                </Badge>
              </div>
            </div>
          ))
        )}
      </div>


      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
