"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { CalendarClock, ClipboardList, Download, FileSignature, Search } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Agreement, Offer, UserApplication } from "@/lib/types";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
  downloadApplicationAgreementPdf,
  errorMessage,
  getApplicationAgreement,
  getApplicationOffer,
  listRentalApplications,
  signApplicationAgreement,
  withdrawRentalApplication,
} from "@/lib/user-api";
import { Card, EmptyState, Toast, useToast } from "@/components/user/ui";

// "DECIDED" alone renders identically for an approval and a rejection --
// genuinely uninformative to the applicant. Mirrors the admin dashboard's
// own applicationDisplay() so both sides describe the same state the same way.
function applicationDisplay(application: UserApplication): { label: string; tone: "warning" | "success" | "danger" | "neutral" } {
  if (application.status === "WITHDRAWN") return { label: "Withdrawn", tone: "neutral" };
  if (application.status === "SUBMITTED") return { label: "Pending Review", tone: "warning" };
  if (application.decision === "APPROVED") return { label: "Approved", tone: "success" };
  if (application.decision === "REJECTED") return { label: "Not Approved", tone: "danger" };
  return { label: "Under Review", tone: "warning" };
}

export function ApplicationsManager() {
  const { toast, showToast } = useToast();
  const [applications, setApplications] = useState<UserApplication[]>([]);
  const [agreements, setAgreements] = useState<Record<number, Agreement | null>>({});
  const [offers, setOffers] = useState<Record<number, Offer | null>>({});
  const [loading, setLoading] = useState(true);
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);
  const [signingId, setSigningId] = useState<number | null>(null);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const loaded = await listRentalApplications();
      setApplications(loaded);

      const approved = loaded.filter((a) => a.decision === "APPROVED");
      const [agreementEntries, offerEntries] = await Promise.all([
        Promise.all(approved.map((a) => getApplicationAgreement(a.id).then((agreement) => [a.id, agreement] as const))),
        Promise.all(approved.map((a) => getApplicationOffer(a.id).then((offer) => [a.id, offer] as const))),
      ]);
      setAgreements(Object.fromEntries(agreementEntries));
      setOffers(Object.fromEntries(offerEntries));
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

  async function handleSignAgreement(applicationId: number) {
    setSigningId(applicationId);
    try {
      const updated = await signApplicationAgreement(applicationId);
      setAgreements((prev) => ({ ...prev, [applicationId]: updated }));
      showToast(updated.status === "SIGNED" ? "Agreement fully signed!" : "Your signature has been recorded.");
    } catch (err) {
      showToast(errorMessage(err, "Could not sign the agreement."), "error");
    } finally {
      setSigningId(null);
    }
  }

  async function handleDownloadPdf(applicationId: number, agreementId: number) {
    setDownloadingId(applicationId);
    try {
      await downloadApplicationAgreementPdf(applicationId, agreementId);
    } catch (err) {
      showToast(errorMessage(err, "Could not download the agreement PDF."), "error");
    } finally {
      setDownloadingId(null);
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
      {applications.map((application) => {
        const display = applicationDisplay(application);
        return (
        <Card key={application.id} className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0 flex-1">
            <Link href={`/account/rent/${application.listingId}`}>
              <div className="flex flex-wrap items-center gap-2">
                <p className="font-heading text-sm font-bold text-primary-900 hover:underline dark:text-white">
                  {application.listingName || application.listingId}
                </p>
                <Badge tone={display.tone}>{display.label}</Badge>
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
            {application.decision === "APPROVED" && (
              <p className="mt-2 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                Check <Link href="/account/rentals" className="underline">My Rentals</Link> for your booking status.
              </p>
            )}
            {application.decision === "APPROVED" && offers[application.id]?.terms.length ? (
              (() => {
                const latestTerms = offers[application.id]!.terms[offers[application.id]!.terms.length - 1];
                return (
                  <p className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {formatCurrency(latestTerms.monthlyRent)}/month · {formatCurrency(latestTerms.depositAmount)} deposit ·{" "}
                    {latestTerms.termMonths} months from {formatDate(latestTerms.startDate)}
                  </p>
                );
              })()
            ) : null}
            {application.decision === "APPROVED" && agreements[application.id] && (
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <FileSignature className="h-3.5 w-3.5 text-primary-600 dark:text-primary-300" />
                {agreements[application.id]!.status === "SIGNED" ? (
                  <Badge tone="success">Agreement fully signed</Badge>
                ) : agreements[application.id]!.signedByRenterAt ? (
                  <Badge tone="warning">You signed — awaiting host signature</Badge>
                ) : (
                  <span className="text-xs text-slate-500 dark:text-slate-400">Your rental agreement is ready to sign.</span>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  loading={downloadingId === application.id}
                  onClick={() => handleDownloadPdf(application.id, agreements[application.id]!.id)}
                >
                  <Download className="h-3.5 w-3.5" /> Download PDF
                </Button>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
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
            {application.decision === "APPROVED" &&
              agreements[application.id] &&
              agreements[application.id]!.status !== "SIGNED" &&
              !agreements[application.id]!.signedByRenterAt && (
                <Button size="sm" loading={signingId === application.id} onClick={() => handleSignAgreement(application.id)}>
                  <FileSignature className="h-3.5 w-3.5" /> Sign Agreement
                </Button>
              )}
          </div>
        </Card>
        );
      })}

      <Toast toast={toast} />
    </div>
  );
}
