"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, ClipboardList, Search } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { BookingChangeRequest, DisclosureRequirement, Offer, PublicListing, UserApplication } from "@/lib/types";
import { applicationStatusTone, bookingChangeRequestStatusTone, bookingChangeTypeLabel } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
  acceptOwnOffer,
  acknowledgeOwnDisclosure,
  declineOwnOffer,
  errorMessage,
  getOwnOffer,
  listMyChangeRequests,
  listOwnAgreementDisclosures,
  listPublicListings,
  listRentalApplications,
  signOwnAgreement,
  submitDateChangeRequest,
  submitFinancialChangeRequest,
  submitPremisesChangeRequest,
  submitShorteningRequest,
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

export function ApplicationsManager() {
  const { toast, showToast } = useToast();
  const [applications, setApplications] = useState<UserApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);

  const [offerFor, setOfferFor] = useState<UserApplication | null>(null);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [disclosures, setDisclosures] = useState<DisclosureRequirement[]>([]);
  const [acknowledgingId, setAcknowledgingId] = useState<number | null>(null);

  const [changeRequests, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [changeType, setChangeType] = useState<"date" | "shorten" | "premises" | "financial" | null>(null);
  const [newStartDate, setNewStartDate] = useState("");
  const [reducedMonths, setReducedMonths] = useState("1");
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
    return changeRequests.find((cr) => cr.agreementId === agreementId && cr.status === "PENDING");
  }

  async function openChangeRequest(type: "date" | "shorten" | "premises" | "financial", application: UserApplication) {
    setChangeType(type);
    setNewStartDate("");
    setReducedMonths("1");
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
    setOfferLoading(true);
    try {
      const loaded = await getOwnOffer(application.id);
      setOffer(loaded);
      if (loaded.agreement) {
        listOwnAgreementDisclosures(loaded.agreement.id)
          .then(setDisclosures)
          .catch(() => {});
      }
    } catch (err) {
      showToast(errorMessage(err, "Could not load your offer."), "error");
      setOfferFor(null);
    } finally {
      setOfferLoading(false);
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
                          {bookingChangeTypeLabel[pending.changeType] ?? pending.changeType} request pending
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
                        <Button
                          size="sm"
                          variant="ghost"
                          loading={withdrawingChangeId === pending.id}
                          onClick={() => handleWithdrawChangeRequest(pending.id)}
                        >
                          Withdraw request
                        </Button>
                      </div>
                    );
                  }
                  if (!offerFor) return null;
                  return (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" variant="outline" onClick={() => openChangeRequest("date", offerFor)}>
                        Request move-in date change
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
                : "Request a new move-in date"
        }
      >
        <div className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            {changeType === "premises"
              ? "Your host has to approve this. Once approved, you'll go through a normal application on the new listing — your current one stays exactly as it is until that new agreement is fully signed."
              : "Your host has to approve this, and you'll both need to re-sign the updated agreement before it takes effect."}
          </p>

          {changeType === "date" && (
            <Field label="New move-in date">
              <input
                type="date"
                value={newStartDate}
                onChange={(e) => setNewStartDate(e.target.value)}
                className={inputClass}
              />
            </Field>
          )}

          {changeType === "shorten" && (
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
          )}

          {changeType === "premises" && (
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
          )}

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
