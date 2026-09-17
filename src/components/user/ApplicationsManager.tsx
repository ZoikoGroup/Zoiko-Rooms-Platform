"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, ClipboardList, Search, ShieldAlert } from "lucide-react";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { BookingChangeRequest, DisclosureRequirement, Offer, PaymentPreview, PublicListing, UserApplication } from "@/lib/types";
import { applicationStatusTone, bookingChangeRequestStatusTone, bookingChangeTypeLabel } from "@/lib/status";
import { addMonths, formatCurrency, formatDate } from "@/lib/utils";
import {
  acceptAlternativeChangeTerms,
  acceptOwnOffer,
  acknowledgeOwnDisclosure,
  declineAlternativeChangeTerms,
  declineOwnOffer,
  errorMessage,
  getObligationAvailableMethods,
  getOwnAgreementPaymentPreview,
  getOwnOffer,
  listMyChangeRequests,
  listOwnAgreementDisclosures,
  listPublicListings,
  listRentalApplications,
  payOwnObligation,
  signOwnAgreement,
  submitDateChangeRequest,
  submitFinancialChangeRequest,
  submitPremisesChangeRequest,
  submitShorteningRequest,
  submitTermShiftRequest,
  withdrawChangeRequest,
  withdrawRentalApplication,
} from "@/lib/user-api";
import { Card, EmptyState, Field, Toast, inputClass, useToast } from "@/components/user/ui";

// Matches the backend's user_sign_agreement allowed-status set exactly
// (crud/leasing.py) -- SENT is the original pre-execution flow;
// PARTIALLY_EXECUTED is the mirror case (provider signed first);
// AMENDMENT_PENDING is reached after any approved booking-change request
// (date shift/extension/shortening) regenerates the agreement and resets
// both signatures. Without all three, a renter has no way to re-sign after
// an amendment even though the backend fully supports it.
const RENTER_SIGNABLE_AGREEMENT_STATUSES: string[] = ["SENT", "PARTIALLY_EXECUTED", "AMENDMENT_PENDING"];

// Human-readable labels for the backend's normalized method classes
// (ZR-ENG-CLR-005 Section 12.1) -- the actual available set for a given
// obligation is always resolved server-side, never hard-coded here.
const PAYMENT_METHOD_LABELS: Record<string, string> = {
  CARD: "Card",
  BANK_DEBIT: "Bank debit",
  BANK_TRANSFER: "Bank transfer",
  PAY_BY_BANK: "Pay by bank",
  DIGITAL_WALLET: "Digital wallet",
  LOCAL_REAL_TIME: "UPI / instant transfer",
  EXTERNAL: "Cash / cheque (recorded by host)",
};

export function ApplicationsManager() {
  const { toast, showToast } = useToast();
  const { identityVerified } = useUserSession();
  const [applications, setApplications] = useState<UserApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);

  const [offerFor, setOfferFor] = useState<UserApplication | null>(null);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [disclosures, setDisclosures] = useState<DisclosureRequirement[]>([]);
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null);
  const [paymentPreview, setPaymentPreview] = useState<PaymentPreview | null>(null);
  const [payingObligationId, setPayingObligationId] = useState<number | null>(null);
  const [obligationMethods, setObligationMethods] = useState<Record<number, string[]>>({});
  const [selectedMethod, setSelectedMethod] = useState<Record<number, string>>({});

  const [changeRequests, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [changeType, setChangeType] = useState<"date" | "shorten" | "premises" | "financial" | "termShift" | null>(null);
  const [newStartDate, setNewStartDate] = useState("");
  const [reducedMonths, setReducedMonths] = useState("1");
  const [newTermMonths, setNewTermMonths] = useState("6");
  const [availableListings, setAvailableListings] = useState<PublicListing[]>([]);
  const [listingsLoading, setListingsLoading] = useState(false);
  const [targetListingId, setTargetListingId] = useState("");
  const [proposedRent, setProposedRent] = useState("");
  const [changeReason, setChangeReason] = useState("");
  const [changeSubmitting, setChangeSubmitting] = useState(false);
  const [changeError, setChangeError] = useState("");
  const [withdrawingChangeId, setWithdrawingChangeId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [applicationsData, changeRequestsData] = await Promise.all([
        listRentalApplications(),
        listMyChangeRequests(),
      ]);
      setApplications(applicationsData);
      setChangeRequests(changeRequestsData);
    } catch (err) {
      showToast(errorMessage(err, "Could not load your applications."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  function pendingChangeRequestFor(agreementId: number | undefined): BookingChangeRequest | undefined {
    if (!agreementId) return undefined;
    return changeRequests.find(
      (cr) => cr.agreementId === agreementId && (cr.status === "AWAITING_HOST" || cr.status === "AWAITING_RENTER"),
    );
  }

  async function openChangeRequest(type: "date" | "shorten" | "premises" | "financial" | "termShift", application: UserApplication) {
    setChangeType(type);
    setNewStartDate("");
    setReducedMonths("1");
    setNewTermMonths("6");
    setTargetListingId("");
    setProposedRent("");
    setChangeReason("");
    setChangeError("");
    if (type === "premises") {
      setAvailableListings([]);
      setListingsLoading(true);
      try {
        const page = await listPublicListings({ limit: 50 });
        setAvailableListings(page.items.filter((l) => l.id !== application.listingId));
      } catch (err) {
        setChangeError(errorMessage(err, "Could not load available listings."));
      } finally {
        setListingsLoading(false);
      }
    }
  }

  async function handleSubmitChangeRequest() {
    if (!offer?.agreement) return;
    setChangeError("");
    setChangeSubmitting(true);
    try {
      if (changeType === "date") {
        if (!newStartDate) {
          setChangeError("Pick a new move-in date.");
          setChangeSubmitting(false);
          return;
        }
        await submitDateChangeRequest(offer.agreement.id, {
          proposedStartDate: newStartDate,
          reason: changeReason.trim(),
        });
      } else if (changeType === "shorten") {
        const months = Number(reducedMonths);
        if (!Number.isInteger(months) || months < 1) {
          setChangeError("Enter a whole number of months (at least 1).");
          setChangeSubmitting(false);
          return;
        }
        await submitShorteningRequest(offer.agreement.id, {
          reducedTermMonths: months,
          reason: changeReason.trim(),
        });
      } else if (changeType === "premises") {
        if (!targetListingId) {
          setChangeError("Choose a listing to move to.");
          setChangeSubmitting(false);
          return;
        }
        await submitPremisesChangeRequest(offer.agreement.id, {
          targetListingId, reason: changeReason.trim(),
        });
      } else if (changeType === "financial") {
        const rent = Number(proposedRent);
        if (!Number.isFinite(rent) || rent <= 0) {
          setChangeError("Enter a valid proposed monthly rent.");
          setChangeSubmitting(false);
          return;
        }
        await submitFinancialChangeRequest(offer.agreement.id, {
          proposedMonthlyRent: rent, reason: changeReason.trim(),
        });
      } else if (changeType === "termShift") {
        const months = Number(newTermMonths);
        if (!newStartDate) {
          setChangeError("Pick a new move-in date.");
          setChangeSubmitting(false);
          return;
        }
        if (!Number.isInteger(months) || months < 1) {
          setChangeError("Enter a whole number of months (at least 1) for the new term.");
          setChangeSubmitting(false);
          return;
        }
        await submitTermShiftRequest(offer.agreement.id, {
          proposedStartDate: newStartDate, newTermMonths: months, reason: changeReason.trim(),
        });
      }
      setChangeType(null);
      showToast("Request submitted for host review.");
      await load();
    } catch (err) {
      setChangeError(errorMessage(err, "Could not submit this request."));
    } finally {
      setChangeSubmitting(false);
    }
  }

  async function handleWithdrawChangeRequest(bcrId: number) {
    setWithdrawingChangeId(bcrId);
    try {
      await withdrawChangeRequest(bcrId);
      showToast("Request withdrawn.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw this request."), "error");
    } finally {
      setWithdrawingChangeId(null);
    }
  }

  async function handleAcceptAlternativeChangeTerms(bcrId: number) {
    setWithdrawingChangeId(bcrId);
    try {
      await acceptAlternativeChangeTerms(bcrId);
      showToast("Accepted — please review and re-sign the updated agreement.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not accept these terms."), "error");
    } finally {
      setWithdrawingChangeId(null);
    }
  }

  async function handleDeclineAlternativeChangeTerms(bcrId: number) {
    setWithdrawingChangeId(bcrId);
    try {
      await declineAlternativeChangeTerms(bcrId);
      showToast("Declined — the request has been withdrawn.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not decline these terms."), "error");
    } finally {
      setWithdrawingChangeId(null);
    }
  }

  useEffect(() => {
    load();
  }, [load]);

  async function handleWithdraw(id: number) {
    setWithdrawingId(id);
    try {
      const updated = await withdrawRentalApplication(id);
      setApplications((prev) => prev.map((a) => (a.id === updated.id ? updated : a)));
      showToast("Application withdrawn.");
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw this application."), "error");
    } finally {
      setWithdrawingId(null);
    }
  }

  async function openOffer(application: UserApplication) {
    setOfferFor(application);
    setOffer(null);
    setDisclosures([]);
    setPaymentPreview(null);
    setObligationMethods({});
    setOfferLoading(true);
    try {
      const loaded = await getOwnOffer(application.id);
      setOffer(loaded);
      if (loaded.agreement) {
        listOwnAgreementDisclosures(loaded.agreement.id)
          .then(setDisclosures)
          .catch(() => {});
        getOwnAgreementPaymentPreview(loaded.agreement.id)
          .then((preview) => {
            setPaymentPreview(preview);
            // ZR-ENG-CLR-005 AC-12: each obligation's real, jurisdiction-resolved
            // methods -- never a hard-coded list on the frontend.
            preview.amountDueNow.forEach((o) => {
              getObligationAvailableMethods(o.id)
                .then(({ methodClasses }) => {
                  setObligationMethods((prev) => ({ ...prev, [o.id]: methodClasses }));
                  setSelectedMethod((prev) => ({ ...prev, [o.id]: prev[o.id] ?? methodClasses[0] }));
                })
                .catch(() => {});
            });
          })
          .catch(() => {});
      }
    } catch (err) {
      showToast(errorMessage(err, "Could not load your offer."), "error");
      setOfferFor(null);
    } finally {
      setOfferLoading(false);
    }
  }

  async function handlePayObligation(obligationId: number) {
    setPayingObligationId(obligationId);
    try {
      await payOwnObligation(obligationId, selectedMethod[obligationId] ?? "CARD");
      showToast("Payment successful.");
      if (offer?.agreement) {
        const [refreshedOffer, refreshedPreview] = await Promise.all([
          getOwnOffer(offerFor!.id),
          getOwnAgreementPaymentPreview(offer.agreement.id),
        ]);
        setOffer(refreshedOffer);
        setPaymentPreview(refreshedPreview);
      }
      load();
    } catch (err) {
      showToast(errorMessage(err, "Payment failed."), "error");
    } finally {
      setPayingObligationId(null);
    }
  }

  async function handleAcknowledgeDisclosure(disclosureId: number) {
    if (!offer?.agreement) return;
    setAcknowledgingId(disclosureId);
    try {
      const updated = await acknowledgeOwnDisclosure(offer.agreement.id, disclosureId);
      setDisclosures((prev) => prev.map((d) => (d.id === updated.id ? updated : d)));
      showToast("Disclosure acknowledged.");
    } catch (err) {
      showToast(errorMessage(err, "Could not acknowledge this disclosure."), "error");
    } finally {
      setAcknowledgingId(null);
    }
  }

  async function handleAcceptOffer() {
    if (!offer) return;
    setActionBusy(true);
    try {
      setOffer(await acceptOwnOffer(offer.id));
      showToast("Offer accepted.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not accept this offer."), "error");
    } finally {
      setActionBusy(false);
    }
  }

  async function handleDeclineOffer() {
    if (!offer) return;
    setActionBusy(true);
    try {
      setOffer(await declineOwnOffer(offer.id));
      showToast("Offer declined.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not decline this offer."), "error");
    } finally {
      setActionBusy(false);
    }
  }

  async function handleSignAgreement() {
    if (!offer?.agreement) return;
    setActionBusy(true);
    try {
      const signed = await signOwnAgreement(offer.agreement.id);
      setOffer({ ...offer, agreement: signed });
      showToast(signed.status === "SIGNED" ? "Agreement fully signed!" : "You've signed — waiting on the host.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not sign this agreement."), "error");
    } finally {
      setActionBusy(false);
    }
  }

  if (loading) return <Loader label="Loading your applications" />;

  if (applications.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-4 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            <ClipboardList className="h-6 w-6" />
          </span>
          <EmptyState message="You have not applied for any rooms yet." />
          <Link href="/account/rent">
            <Button size="sm">
              <Search className="h-4 w-4" /> Browse available rooms
            </Button>
          </Link>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-3">
      {applications.map((application) => (
        <Card key={application.id} className="flex flex-wrap items-center justify-between gap-4">
          <Link href={`/account/rent/${application.listingId}`} className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-heading text-sm font-bold text-primary-900 hover:underline dark:text-white">
                {application.listingName || application.listingId}
              </p>
              <Badge tone={applicationStatusTone[application.status] ?? "neutral"}>{application.status}</Badge>
              {application.agreementStatus && <Badge tone="neutral">Agreement: {application.agreementStatus}</Badge>}
              {!application.agreementStatus && application.offerStatus && (
                <Badge tone="neutral">Offer: {application.offerStatus}</Badge>
              )}
            </div>
            <p className="mt-0.5 text-xs text-slate-400">Application #{application.listingId}</p>
            <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
              <span className="flex items-center gap-1">
                <CalendarClock className="h-3 w-3" /> Submitted {formatDate(application.submittedAt)}
              </span>
              {application.desiredMoveIn && <span>Move-in {formatDate(application.desiredMoveIn)}</span>}
            </p>
            {application.message && (
              <p className="mt-2 max-w-xl text-xs text-slate-500 dark:text-slate-400">“{application.message}”</p>
            )}
            {/* ZR-ENG-CLR-012 Section 5/AC-04: identity is only required
               before CONFIRMED booking (agreement creation), not before
               applying -- once the offer is accepted and waiting on an
               agreement, an unverified renter is the one real, common
               reason nothing moves further, so surface it here instead of
               leaving them to find out from a stalled admin-side 409. */}
            {application.offerStatus === "ACCEPTED" && !application.agreementStatus && !identityVerified && (
              <Link
                href="/account/identity"
                className="mt-2 flex items-center gap-1.5 text-xs font-medium text-amber-700 hover:underline dark:text-amber-400"
              >
                <ShieldAlert className="h-3.5 w-3.5" /> Verify your identity to keep this moving toward your agreement
              </Link>
            )}
          </Link>

          <div className="flex shrink-0 items-center gap-2">
            {application.offerId && (
              <Button size="sm" variant="outline" onClick={() => openOffer(application)}>
                View offer
              </Button>
            )}
            {application.status === "SUBMITTED" && (
              <Button
                size="sm"
                variant="outline"
                loading={withdrawingId === application.id}
                onClick={() => handleWithdraw(application.id)}
              >
                Withdraw
              </Button>
            )}
          </div>
        </Card>
      ))}

      <Modal open={Boolean(offerFor)} onClose={() => setOfferFor(null)} title="Your offer">
        {offerLoading ? (
          <Loader label="Loading your offer" />
        ) : offer ? (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <Badge tone="neutral">Offer: {offer.status}</Badge>
              {offer.agreement && <Badge tone="neutral">Agreement: {offer.agreement.status}</Badge>}
            </div>

            {offer.terms.length > 0 && (
              <div className="rounded-xl bg-slate-50 p-4 text-sm dark:bg-slate-800/60">
                {(() => {
                  const latest = offer.terms[offer.terms.length - 1];
                  return (
                    <dl className="grid grid-cols-2 gap-3">
                      <div>
                        <dt className="text-xs text-slate-400">Monthly rent</dt>
                        <dd className="font-semibold text-primary-900 dark:text-white">
                          {formatCurrency(latest.monthlyRent)}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-400">Deposit</dt>
                        <dd className="font-semibold text-primary-900 dark:text-white">
                          {formatCurrency(latest.depositAmount)}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-400">Start date</dt>
                        <dd className="font-semibold text-primary-900 dark:text-white">{formatDate(latest.startDate)}</dd>
                      </div>
                      <div>
                        <dt className="text-xs text-slate-400">Term</dt>
                        <dd className="font-semibold text-primary-900 dark:text-white">{latest.termMonths} months</dd>
                      </div>
                    </dl>
                  );
                })()}
              </div>
            )}

            {offer.status === "SENT" && (
              <div className="flex justify-end gap-2">
                <Button variant="outline" loading={actionBusy} onClick={handleDeclineOffer}>
                  Decline
                </Button>
                <Button loading={actionBusy} onClick={handleAcceptOffer}>
                  Accept offer
                </Button>
              </div>
            )}

            {offer.agreement && disclosures.length > 0 && (
              <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Required disclosures
                </p>
                {disclosures.map((d) => (
                  <div key={d.id} className="flex items-center justify-between gap-2">
                    <span className="text-sm text-slate-700 dark:text-slate-200">{d.title || d.disclosureType}</span>
                    {d.status === "ACKNOWLEDGED" ? (
                      <Badge tone="success">Acknowledged</Badge>
                    ) : d.status === "DELIVERED" ? (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={acknowledgingId === d.id}
                        onClick={() => handleAcknowledgeDisclosure(d.id)}
                      >
                        Acknowledge
                      </Button>
                    ) : (
                      <Badge tone="warning">Not yet delivered</Badge>
                    )}
                  </div>
                ))}
              </div>
            )}

            {offer.agreement &&
              RENTER_SIGNABLE_AGREEMENT_STATUSES.includes(offer.agreement.status) &&
              !offer.agreement.signedByRenterAt && (
              <div className="flex justify-end">
                <Button loading={actionBusy} onClick={handleSignAgreement}>
                  Sign agreement
                </Button>
              </div>
            )}
            {offer.agreement?.signedByRenterAt && offer.agreement.status !== "SIGNED" && (
              <p className="text-xs text-slate-500 dark:text-slate-400">
                You&apos;ve signed — waiting on the host to countersign.
              </p>
            )}

            {["PAYMENT_IN_PROGRESS", "PAYMENT_PENDING"].includes(offer.agreement?.status ?? "") && (
              <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Payment due
                </p>
                {paymentPreview === null ? (
                  <p className="text-xs text-slate-400">Loading...</p>
                ) : paymentPreview.amountDueNow.length === 0 ? (
                  <p className="text-xs text-slate-400">Nothing due right now.</p>
                ) : (
                  paymentPreview.amountDueNow.map((o) => (
                    <div key={o.id} className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm text-slate-700 dark:text-slate-200">
                        {o.obligationType === "RENT" ? "Rent" : o.obligationType === "DEPOSIT" ? "Deposit" : o.obligationType}
                        {" — "}
                        {formatCurrency(o.amount, o.currency)}
                      </span>
                      {o.status === "PAID" ? (
                        <Badge tone="success">Paid</Badge>
                      ) : (
                        <div className="flex items-center gap-2">
                          <select
                            className={`${inputClass} !w-auto py-1.5 text-xs`}
                            value={selectedMethod[o.id] ?? ""}
                            onChange={(e) => setSelectedMethod((prev) => ({ ...prev, [o.id]: e.target.value }))}
                            disabled={!obligationMethods[o.id]?.length}
                          >
                            {(obligationMethods[o.id] ?? []).map((m) => (
                              <option key={m} value={m}>
                                {PAYMENT_METHOD_LABELS[m] ?? m}
                              </option>
                            ))}
                          </select>
                          <Button
                            size="sm"
                            loading={payingObligationId === o.id}
                            disabled={!selectedMethod[o.id]}
                            onClick={() => handlePayObligation(o.id)}
                          >
                            Pay now
                          </Button>
                        </div>
                      )}
                    </div>
                  ))
                )}
              </div>
            )}
            {offer.agreement?.status === "SIGNED" && (
              <div className="space-y-3">
                <p className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                  Fully signed — check your rentals for move-in details.
                </p>
                {(() => {
                  const pending = pendingChangeRequestFor(offer.agreement?.id);
                  if (pending) {
                    return (
                      <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                        <Badge tone={bookingChangeRequestStatusTone[pending.status] ?? "neutral"}>
                          {bookingChangeTypeLabel[pending.changeType] ?? pending.changeType}
                          {" — "}
                          {pending.status === "AWAITING_RENTER" ? "host proposed different terms" : "request pending"}
                        </Badge>
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                          {pending.changeType === "PREMISES_CHANGE" ? (
                            `Move to "${pending.targetListingName || pending.targetListingId}"`
                          ) : pending.changeType === "FINANCIAL_CHANGE" ? (
                            `${formatCurrency(pending.originalMonthlyRent ?? 0)} → ${formatCurrency(pending.proposedMonthlyRent ?? 0)}/month`
                          ) : (
                            <>
                              Proposed move-in: {formatDate(pending.proposedStartDate)}
                              {pending.changeType === "SHORTENING" && pending.proposedEndDate &&
                                ` — new end date ${formatDate(pending.proposedEndDate)}`}
                            </>
                          )}
                        </p>
                        {pending.status === "AWAITING_RENTER" ? (
                          <div className="flex gap-2">
                            <Button
                              size="sm"
                              variant="primary"
                              className="flex-1"
                              loading={withdrawingChangeId === pending.id}
                              onClick={() => handleAcceptAlternativeChangeTerms(pending.id)}
                            >
                              Accept
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              className="flex-1"
                              loading={withdrawingChangeId === pending.id}
                              onClick={() => handleDeclineAlternativeChangeTerms(pending.id)}
                            >
                              Decline
                            </Button>
                          </div>
                        ) : (
                          <Button
                            size="sm"
                            variant="ghost"
                            loading={withdrawingChangeId === pending.id}
                            onClick={() => handleWithdrawChangeRequest(pending.id)}
                          >
                            Withdraw request
                          </Button>
                        )}
                      </div>
                    );
                  }
                  if (!offerFor) return null;
                  return (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" onClick={() => openChangeRequest("date", offerFor)}>
                        Request move-in date change
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openChangeRequest("termShift", offerFor)}>
                        Request date &amp; term change together
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openChangeRequest("shorten", offerFor)}>
                        Request to shorten stay
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openChangeRequest("premises", offerFor)}>
                        Request a different room/property
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openChangeRequest("financial", offerFor)}>
                        Request a rent change
                      </Button>
                    </div>
                  );
                })()}
                <p className="text-xs text-slate-400">
                  Only usable before you&apos;ve actually moved in — once you have, extend or manage your stay from your
                  rentals page instead.
                </p>
              </div>
            )}
          </div>
        ) : null}
      </Modal>

      <Modal
        open={Boolean(changeType)}
        onClose={() => setChangeType(null)}
        title={
          changeType === "shorten"
            ? "Request to shorten your stay"
            : changeType === "premises"
              ? "Request a different room/property"
              : changeType === "financial"
                ? "Request a rent change"
                : changeType === "termShift"
                  ? "Request a new move-in date and term"
                  : "Request a new move-in date"
        }
      >
        <div className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            {changeType === "premises"
              ? "Your host has to approve this. Once approved, you'll go through a normal application on the new listing — your current one stays exactly as it is until that new agreement is fully signed."
              : "Your host has to approve this, and you'll both need to re-sign the updated agreement before it takes effect."}
          </p>

          {changeType === "date" && (() => {
            const currentStart = offer?.terms.length ? offer.terms[offer.terms.length - 1].startDate : null;
            const showDelta = Boolean(currentStart && newStartDate);
            return (
              <>
                <Field label="New move-in date">
                  <input
                    type="date"
                    value={newStartDate}
                    onChange={(e) => setNewStartDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                {/* Section 1 doctrine: show old vs proposed before consent. */}
                {showDelta && (
                  <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Current move-in</span>
                      <span className="font-semibold text-primary-900 dark:text-white">{formatDate(currentStart!)}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Proposed move-in</span>
                      <span className="font-semibold text-primary-900 dark:text-white">{formatDate(newStartDate)}</span>
                    </div>
                  </div>
                )}
              </>
            );
          })()}

          {changeType === "termShift" && (() => {
            const terms = offer?.terms.length ? offer.terms[offer.terms.length - 1] : null;
            const months = Number(newTermMonths);
            const currentEnd = terms ? addMonths(terms.startDate, terms.termMonths) : null;
            const proposedEnd = newStartDate && Number.isInteger(months) && months >= 1 ? addMonths(newStartDate, months) : null;
            return (
              <>
                <Field label="New move-in date">
                  <input
                    type="date"
                    value={newStartDate}
                    onChange={(e) => setNewStartDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label="New term (months)">
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={newTermMonths}
                    onChange={(e) => setNewTermMonths(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                {terms && (
                  <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Current move-in / end</span>
                      <span className="font-semibold text-primary-900 dark:text-white">
                        {formatDate(terms.startDate)} → {currentEnd ? formatDate(currentEnd) : "—"}
                      </span>
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Proposed move-in / end</span>
                      <span className="font-semibold text-primary-900 dark:text-white">
                        {newStartDate ? formatDate(newStartDate) : "—"} → {proposedEnd ? formatDate(proposedEnd) : "—"}
                      </span>
                    </div>
                  </div>
                )}
              </>
            );
          })()}

          {changeType === "shorten" && (() => {
            const terms = offer?.terms.length ? offer.terms[offer.terms.length - 1] : null;
            const months = Number(reducedMonths);
            const currentEnd = terms ? addMonths(terms.startDate, terms.termMonths) : null;
            const proposedEnd = terms && Number.isInteger(months) && months >= 1 && months < terms.termMonths
              ? addMonths(terms.startDate, terms.termMonths - months) : null;
            return (
              <>
                <Field label="Reduce term by (months)" hint="Whole months to remove from your current term.">
                  <input
                    type="number"
                    min={1}
                    step={1}
                    value={reducedMonths}
                    onChange={(e) => setReducedMonths(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                {currentEnd && (
                  <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Current lease end</span>
                      <span className="font-semibold text-primary-900 dark:text-white">{formatDate(currentEnd)}</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Proposed lease end</span>
                      <span className="font-semibold text-primary-900 dark:text-white">
                        {proposedEnd ? formatDate(proposedEnd) : "—"}
                      </span>
                    </div>
                  </div>
                )}
                {/* Section 2/11 doctrine: "A shortening must not automatically
                    release part of a deposit unless the lawful deposit
                    instrument and custody model support a partial release."
                    This platform has no partial-release flow yet, so the
                    honest disclosure is that none happens here. */}
                <p className="text-xs text-slate-400">
                  Your security deposit is not affected by this change.
                </p>
              </>
            );
          })()}

          {changeType === "premises" && (() => {
            const target = availableListings.find((l) => l.id === targetListingId);
            return (
              <>
                <Field label="Move to">
                  {listingsLoading ? (
                    <p className="text-xs text-slate-400">Loading listings...</p>
                  ) : (
                    <select
                      value={targetListingId}
                      onChange={(e) => setTargetListingId(e.target.value)}
                      className={inputClass}
                    >
                      <option value="">Choose a listing...</option>
                      {availableListings.map((l) => (
                        <option key={l.id} value={l.id}>
                          {l.name} — {l.city}
                        </option>
                      ))}
                    </select>
                  )}
                </Field>
                {target && (
                  <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Listed reference price</span>
                      <span className="font-semibold text-primary-900 dark:text-white">
                        {formatCurrency(target.pricePerNight)}/night
                      </span>
                    </div>
                    <p className="mt-1 text-slate-400">
                      Not your final rent -- your host sets fresh monthly terms once this request is approved.
                    </p>
                  </div>
                )}
              </>
            );
          })()}

          {changeType === "financial" && (() => {
            const currentRent = offer?.terms.length ? offer.terms[offer.terms.length - 1].monthlyRent : null;
            const proposed = Number(proposedRent);
            const showDelta = currentRent != null && Number.isFinite(proposed) && proposed > 0;
            return (
              <>
                <Field label="Proposed monthly rent (₹)">
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={proposedRent}
                    onChange={(e) => setProposedRent(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                {/* ZR-ENG-CLR-008 Section 1 doctrine: "No party may be
                    financially worse off by a hidden recalculation. The UI
                    must show old terms, proposed new terms and the net delta
                    before consent." This is the one change type that
                    actually has a real financial delta to show. */}
                {showDelta && (
                  <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                    <div className="flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Current rent</span>
                      <span className="font-semibold text-primary-900 dark:text-white">{formatCurrency(currentRent!)}/mo</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between">
                      <span className="text-slate-500 dark:text-slate-400">Proposed rent</span>
                      <span className="font-semibold text-primary-900 dark:text-white">{formatCurrency(proposed)}/mo</span>
                    </div>
                    <div className="mt-1 flex items-center justify-between border-t border-slate-200 pt-1 dark:border-slate-700">
                      <span className="text-slate-500 dark:text-slate-400">Difference</span>
                      <span className={proposed > currentRent! ? "font-semibold text-accent-700 dark:text-accent-400" : "font-semibold text-emerald-600 dark:text-emerald-400"}>
                        {proposed > currentRent! ? "+" : ""}
                        {formatCurrency(proposed - currentRent!)}/mo
                      </span>
                    </div>
                  </div>
                )}
                <p className="text-xs text-slate-400">
                  Your security deposit is not automatically changed by a rent change.
                </p>
              </>
            );
          })()}

          <Field label="Reason (optional)">
            <textarea
              value={changeReason}
              onChange={(e) => setChangeReason(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>

          {changeError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {changeError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setChangeType(null)}>
              Cancel
            </Button>
            <Button type="button" loading={changeSubmitting} onClick={handleSubmitChangeRequest}>
              Submit request
            </Button>
          </div>
        </div>
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
