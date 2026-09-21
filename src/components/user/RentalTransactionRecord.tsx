"use client";

import { useEffect, useState } from "react";
import { ClipboardList } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Loader } from "@/components/ui/Loader";
import { RentalTransactionRecord as RentalTransactionRecordData } from "@/lib/types";
import { getHostedRentalTransactionRecord, getRentalTransactionRecord } from "@/lib/user-api";
import {
  agreementStatusLabel,
  agreementStatusTone,
  amendmentStatusLabel,
  amendmentStatusTone,
  applicationStatusTone,
  depositStatusLabel,
  depositStatusTone,
  obligationStatusLabel,
  obligationStatusTone,
  occupancyStatusTone,
  offerStatusLabel,
  offerStatusTone,
  renterVerificationStatusTone,
  simulatedPaymentStatusTone,
  subletRequestStatusLabel,
  subletRequestStatusTone,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import { Card, EmptyState } from "@/components/user/ui";

/** Rental Transaction Record wireframe: a read-only, computed composite over
 *  this occupancy's own Application/Offer/Agreement, payments, handover,
 *  sublet and termination records -- nothing here is a separate source of
 *  truth, it's all fetched fresh from backend/app/crud/
 *  rental_transaction_record.py on every open. `role` picks which endpoint
 *  (and therefore which authorization check) applies; the host endpoint
 *  never returns identityVerification, so that section simply doesn't
 *  render for a host viewer. */
export function RentalTransactionRecord({ occupancyId, role }: { occupancyId: number; role: "renter" | "host" }) {
  const [record, setRecord] = useState<RentalTransactionRecordData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(false);
    const fetcher = role === "host" ? getHostedRentalTransactionRecord : getRentalTransactionRecord;
    fetcher(occupancyId)
      .then((data) => {
        if (!cancelled) setRecord(data);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [occupancyId, role]);

  if (loading) return <Loader label="Loading transaction record..." />;
  if (error || !record) return <EmptyState message="This transaction record is unavailable right now." />;

  const { occupancy, application, agreement, offer } = {
    occupancy: record.occupancy,
    application: record.application,
    offer: record.application?.offer ?? null,
    agreement: record.application?.offer?.agreement ?? null,
  };

  return (
    <div className="space-y-4">
      <Card>
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Rental summary</h3>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Badge tone={occupancyStatusTone[occupancy.status] ?? "neutral"}>{occupancy.status.replace(/_/g, " ")}</Badge>
          <span className="text-xs text-slate-500 dark:text-slate-400">
            {occupancy.propertyAddress || occupancy.listingName} — Room #{occupancy.roomId}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400 sm:grid-cols-3">
          <span>Moved in: {occupancy.moveInDate ? formatDate(occupancy.moveInDate) : "—"}</span>
          <span>Expected end: {occupancy.expectedEndDate ? formatDate(occupancy.expectedEndDate) : "—"}</span>
          <span>Moved out: {occupancy.moveOutDate ? formatDate(occupancy.moveOutDate) : "—"}</span>
        </div>
      </Card>

      <Card>
        <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Application, offer &amp; agreement</h3>
        {application ? (
          <div className="mt-2 space-y-2 text-xs text-slate-500 dark:text-slate-400">
            <div className="flex items-center gap-2">
              <Badge tone={applicationStatusTone[application.status] ?? "neutral"}>{application.status}</Badge>
              <span>Applied {formatDate(application.submittedAt)}</span>
            </div>
            {offer && (
              <div className="flex items-center gap-2">
                <Badge tone={offerStatusTone[offer.status] ?? "neutral"}>{offerStatusLabel[offer.status] ?? offer.status}</Badge>
                {offer.terms[0] && (
                  <span>
                    {formatCurrency(offer.terms[0].monthlyRent, offer.terms[0].currency)}/month · {offer.terms[0].termMonths} months
                  </span>
                )}
              </div>
            )}
            {agreement && (
              <div className="flex items-center gap-2">
                <Badge tone={agreementStatusTone[agreement.status] ?? "neutral"}>
                  {agreementStatusLabel[agreement.status] ?? agreement.status}
                </Badge>
                {agreement.signedByRenterAt && <span>Renter signed {formatDate(agreement.signedByRenterAt)}</span>}
                {agreement.signedByProviderAt && <span>Host signed {formatDate(agreement.signedByProviderAt)}</span>}
              </div>
            )}
          </div>
        ) : (
          <p className="mt-2 text-xs text-slate-400">No application on file.</p>
        )}
      </Card>

      {record.amendments.length > 0 && (
        <Card>
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Amendments</h3>
          <div className="mt-2 space-y-2">
            {record.amendments.map((amendment) => (
              <div key={amendment.id} className="flex items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>{amendment.amendmentType ?? "Amendment"} — {amendment.reason || "No reason given"}</span>
                <Badge tone={amendmentStatusTone[amendment.status] ?? "neutral"}>
                  {amendmentStatusLabel[amendment.status] ?? amendment.status}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card>
        <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Payments &amp; financial history</h3>
        {record.obligations.length === 0 ? (
          <p className="mt-2 text-xs text-slate-400">No obligations recorded yet.</p>
        ) : (
          <div className="mt-2 space-y-1.5">
            {record.obligations.map((obligation) => (
              <div key={obligation.id} className="flex items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>
                  {obligation.obligationType} — {formatCurrency(obligation.amount, obligation.currency)} due {formatDate(obligation.dueDate)}
                </span>
                <Badge tone={obligationStatusTone[obligation.status as keyof typeof obligationStatusTone] ?? "neutral"}>
                  {obligationStatusLabel[obligation.status as keyof typeof obligationStatusLabel] ?? obligation.status}
                </Badge>
              </div>
            ))}
          </div>
        )}
        {record.payments.length > 0 && (
          <div className="mt-3 space-y-1.5 border-t border-slate-100 pt-3 dark:border-white/10">
            {record.payments.map((payment) => (
              <div key={payment.id} className="flex items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>{formatCurrency(payment.amount, payment.currency)} on {formatDate(payment.createdAt)}</span>
                <Badge tone={simulatedPaymentStatusTone[payment.status] ?? "neutral"}>{payment.status}</Badge>
              </div>
            ))}
          </div>
        )}
        {record.deposit && (
          <div className="mt-3 flex items-center justify-between gap-2 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-white/10 dark:text-slate-400">
            <span>
              Deposit: {formatCurrency(
                record.deposit.heldAmount,
                record.obligations.find((o) => o.id === record.deposit?.obligationId)?.currency
              )}
            </span>
            <Badge tone={depositStatusTone[record.deposit.status] ?? "neutral"}>
              {depositStatusLabel[record.deposit.status] ?? record.deposit.status}
            </Badge>
          </div>
        )}
      </Card>

      {(record.handoverEvents.length > 0 || record.activationDecisions.length > 0) && (
        <Card>
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Move-in &amp; handover</h3>
          <div className="mt-2 space-y-1.5 text-xs text-slate-500 dark:text-slate-400">
            {record.handoverEvents.map((event) => (
              <p key={event.id}>{event.eventType.replace(/_/g, " ")} — {formatDate(event.createdAt)}</p>
            ))}
            {record.activationDecisions.map((decision) => (
              <p key={decision.id}>Activation: {decision.outcome} — {formatDate(decision.evaluatedAt)}</p>
            ))}
          </div>
        </Card>
      )}

      {record.subletRequests.length > 0 && (
        <Card>
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Sublet activity</h3>
          <div className="mt-2 space-y-2">
            {record.subletRequests.map((sr) => (
              <div key={sr.id} className="flex items-center justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>{sr.arrangementType.replace(/_/g, " ")} — proposed {sr.proposedRenterName || `party #${sr.proposedRenterPartyId}`}</span>
                <Badge tone={subletRequestStatusTone[sr.status] ?? "neutral"}>{subletRequestStatusLabel[sr.status] ?? sr.status}</Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {(record.terminationCases.length > 0 || record.terminationRecord) && (
        <Card>
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Move-out &amp; termination</h3>
          <div className="mt-2 space-y-1.5 text-xs text-slate-500 dark:text-slate-400">
            {record.terminationCases.map((tc) => (
              <p key={tc.id}>{tc.causeCode.replace(/_/g, " ")} — {tc.status.replace(/_/g, " ")}</p>
            ))}
            {record.terminationRecord && (
              <p>
                {record.terminationRecord.basis.replace(/_/g, " ")}
                {record.terminationRecord.physicalMoveOutDate && ` — moved out ${formatDate(record.terminationRecord.physicalMoveOutDate)}`}
              </p>
            )}
          </div>
        </Card>
      )}

      <Card>
        <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Verification</h3>
        <div className="mt-2 flex flex-wrap gap-2">
          {record.propertyVerification && (
            <Badge tone={renterVerificationStatusTone[record.propertyVerification.status] ?? "neutral"}>
              Property: {record.propertyVerification.status.replace(/_/g, " ")}
            </Badge>
          )}
          {record.authorityToList && (
            <Badge tone={renterVerificationStatusTone[record.authorityToList.status] ?? "neutral"}>
              Authority to list: {record.authorityToList.status.replace(/_/g, " ")}
            </Badge>
          )}
          {record.identityVerification && (
            <Badge tone={renterVerificationStatusTone[record.identityVerification.status] ?? "neutral"}>
              Your identity: {record.identityVerification.status.replace(/_/g, " ")}
            </Badge>
          )}
        </div>
      </Card>

      {record.timeline.length > 0 && (
        <Card>
          <h3 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Timeline</h3>
          <div className="mt-2 space-y-1.5">
            {record.timeline.map((entry, i) => (
              <div key={i} className="flex items-start justify-between gap-2 text-xs text-slate-500 dark:text-slate-400">
                <span>{entry.eventType.replace(/_/g, " ").replace(/\./g, " · ")}</span>
                <span className="shrink-0 text-slate-400">{formatDate(entry.timestamp)}</span>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}
