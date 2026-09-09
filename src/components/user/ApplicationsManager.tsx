"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, ClipboardList, Search } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Offer, UserApplication } from "@/lib/types";
import { applicationStatusTone } from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
  acceptOwnOffer,
  declineOwnOffer,
  errorMessage,
  getOwnOffer,
  listRentalApplications,
  signOwnAgreement,
  withdrawRentalApplication,
} from "@/lib/user-api";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

export function ApplicationsManager() {
  const { toast, showToast } = useToast();
  const [applications, setApplications] = useState<UserApplication[]>([]);
  const [loading, setLoading] = useState(true);
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);

  const [offerFor, setOfferFor] = useState<UserApplication | null>(null);
  const [offer, setOffer] = useState<Offer | null>(null);
  const [offerLoading, setOfferLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setApplications(await listRentalApplications());
    } catch (err) {
      showToast(errorMessage(err, "Could not load your applications."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

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
    setOfferLoading(true);
    try {
      setOffer(await getOwnOffer(application.id));
    } catch (err) {
      showToast(errorMessage(err, "Could not load your offer."), "error");
      setOfferFor(null);
    } finally {
      setOfferLoading(false);
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

            {offer.agreement && offer.agreement.status === "SENT" && !offer.agreement.signedByRenterAt && (
              <div className="flex justify-end">
                <Button loading={actionBusy} onClick={handleSignAgreement}>
                  Sign agreement
                </Button>
              </div>
            )}
            {offer.agreement?.signedByRenterAt && offer.agreement.status !== "SIGNED" && (
              <p className="text-xs text-slate-500 dark:text-slate-400">
                You've signed — waiting on the host to countersign.
              </p>
            )}
            {offer.agreement?.status === "SIGNED" && (
              <p className="text-xs font-medium text-emerald-600 dark:text-emerald-400">
                Fully signed — check your rentals for move-in details.
              </p>
            )}
          </div>
        ) : null}
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
