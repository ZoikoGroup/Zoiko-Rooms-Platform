"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, FileText, ShieldCheck, XCircle } from "lucide-react";
import { AdminIdentityVerification, IdentityVerificationStatus } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { identityStatusLabel, identityStatusTone } from "@/lib/status";
import { documentCategoryLabel, documentTypeLabel } from "@/lib/identity-documents";
import { formatDate } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const STATUS_FILTERS: { value: IdentityVerificationStatus | "all"; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "verified", label: "Verified" },
  { value: "rejected", label: "Rejected" },
  { value: "all", label: "All" },
];

function documentUrl(id: number): string {
  return `${API_URL}/api/identity-verifications/${id}/document`;
}

export function IdentityVerificationsManager() {
  const [filter, setFilter] = useState<IdentityVerificationStatus | "all">("pending");
  const [records, setRecords] = useState<AdminIdentityVerification[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [rejectTarget, setRejectTarget] = useState<AdminIdentityVerification | null>(null);
  const [rejectNotes, setRejectNotes] = useState("");
  const [toast, setToast] = useState("");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const query = filter === "all" ? "" : `?status=${filter}`;
      const data = await apiClientFetch<AdminIdentityVerification[]>(`/api/identity-verifications${query}`);
      setRecords(data);
    } catch {
      showToast("Failed to load identity verifications");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    load();
  }, [load]);

  async function approve(id: number) {
    setBusyId(id);
    try {
      await apiClientFetch<AdminIdentityVerification>(`/api/identity-verifications/${id}/verify`, { method: "POST" });
      showToast("Identity verification approved");
      await load();
    } catch {
      showToast("Failed to approve this verification");
    } finally {
      setBusyId(null);
    }
  }

  function openReject(record: AdminIdentityVerification) {
    setRejectTarget(record);
    setRejectNotes("");
  }

  async function submitReject() {
    if (!rejectTarget) return;
    setBusyId(rejectTarget.id);
    try {
      await apiClientFetch<AdminIdentityVerification>(`/api/identity-verifications/${rejectTarget.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ notes: rejectNotes.trim() }),
      });
      showToast("Identity verification rejected");
      setRejectTarget(null);
      await load();
    } catch {
      showToast("Failed to reject this verification");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">
            Identity Verifications
          </h2>
        </div>
        <div className="flex gap-1.5 rounded-full bg-slate-100 p-1 dark:bg-white/5">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`rounded-full px-3 py-1.5 text-xs font-semibold transition-colors ${
                filter === f.value
                  ? "bg-white text-primary-800 shadow-sm dark:bg-slate-800 dark:text-white"
                  : "text-slate-500 hover:text-primary-700 dark:text-slate-400"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Review a USER&apos;s uploaded document, then approve or reject their identity verification.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : records.length === 0 ? (
          <p className="text-sm text-slate-400 dark:text-slate-400">No identity verifications match this filter.</p>
        ) : (
          records.map((record) => (
            <div
              key={record.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  Party #{record.partyId} —{" "}
                  {record.documentType === "other" && record.customDocumentName
                    ? record.customDocumentName
                    : documentTypeLabel[record.documentType] ?? record.documentType}
                </p>
                <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
                  <span>{documentCategoryLabel[record.documentCategory] ?? record.documentCategory}</span>
                  <span>Submitted {formatDate(record.createdAt)}</span>
                  {record.encryptedReference && <span>Ref: {record.encryptedReference}</span>}
                  {record.hasDocument ? (
                    <a
                      href={documentUrl(record.id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300"
                    >
                      <FileText className="h-3 w-3" /> View document
                    </a>
                  ) : (
                    <span className="text-slate-400">No document uploaded</span>
                  )}
                </p>
                {record.status === "rejected" && record.verifierNotes && (
                  <p className="mt-1 text-xs text-accent-600">Rejection notes: {record.verifierNotes}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={identityStatusTone[record.status]}>{identityStatusLabel[record.status]}</Badge>
                {record.status === "pending" && (
                  <>
                    <Button
                      size="sm"
                      variant="primary"
                      loading={busyId === record.id}
                      onClick={() => approve(record.id)}
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => openReject(record)}>
                      <XCircle className="h-3.5 w-3.5" /> Reject
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <Modal open={Boolean(rejectTarget)} onClose={() => setRejectTarget(null)} title="Reject identity verification">
        <div className="space-y-3.5">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Optionally tell the user why their document was rejected — they will see this note.
          </p>
          <textarea
            value={rejectNotes}
            onChange={(e) => setRejectNotes(e.target.value)}
            rows={4}
            placeholder="e.g. The uploaded document is expired. Please submit a current one."
            className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setRejectTarget(null)}>
              Cancel
            </Button>
            <Button variant="accent" loading={busyId === rejectTarget?.id} onClick={submitReject}>
              Reject verification
            </Button>
          </div>
        </div>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
