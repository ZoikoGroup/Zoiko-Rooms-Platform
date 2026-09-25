"use client";

import { FormEvent, useEffect, useState } from "react";
import { CheckCircle2, ShieldCheck, Upload, XCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { ACCEPTED_DOCUMENT_EXTENSIONS, MAX_DOCUMENT_SIZE_MB } from "@/lib/identity-documents";
import { PropertyVerification } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { declareHostedPropertyVerification, errorMessage, listHostedRoomPropertyVerifications } from "@/lib/user-api";

/** "Is the property/address itself real and evidenced" -- a separate claim
 *  from identity verification (who the lister is) and payment recipient
 *  authority (who gets paid). Previously only reachable once, inline in
 *  ListARoomWizard's review step, with no way back in for a room created
 *  before that step existed or where it was skipped. Mirrors
 *  PaymentRecipientSetup's own "current status card + submit-again form"
 *  shape so a host can always get back here from My listings. */
export function PropertyVerificationManager({ roomId }: { roomId: number }) {
  const { toast, showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState<PropertyVerification[]>([]);
  const [showForm, setShowForm] = useState(false);

  function load() {
    setLoading(true);
    listHostedRoomPropertyVerifications(roomId)
      .then(setRecords)
      .catch((err) => showToast(errorMessage(err, "Could not load property verification status."), "error"))
      .finally(() => setLoading(false));
  }

  useEffect(load, [roomId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <Loader label="Loading property verification status" />;

  const current = [...records].sort((a, b) => b.id - a.id)[0] ?? null;
  const now = Date.now();
  const currentIsLiveVerified =
    current?.status === "verified" && (!current.expiresAt || new Date(current.expiresAt).getTime() > now);

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Upload evidence that this property/address is real -- a lease, utility bill, title deed, or similar document
        showing the property&apos;s address. An automated scan checks it against this room&apos;s registered address
        and verifies it immediately when it matches; otherwise a Zoiko admin reviews it.
      </p>

      {current && !showForm && (
        <Card
          className={
            currentIsLiveVerified
              ? "!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20"
              : current.status === "pending"
                ? "!bg-amber-50 !ring-amber-200 dark:!bg-amber-500/10 dark:!ring-amber-500/20"
                : "!bg-rose-50 !ring-rose-200 dark:!bg-rose-500/10 dark:!ring-rose-500/20"
          }
        >
          <div className="flex items-center gap-2">
            {currentIsLiveVerified ? (
              <CheckCircle2 className="h-4 w-4 text-emerald-700 dark:text-emerald-300" aria-hidden="true" />
            ) : (
              <XCircle className="h-4 w-4 text-amber-700 dark:text-amber-300" aria-hidden="true" />
            )}
            <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              {currentIsLiveVerified
                ? "Property verified"
                : current.status === "pending"
                  ? "Awaiting admin verification"
                  : current.status === "additional_evidence_required"
                    ? "More evidence needed"
                    : current.status === "revoked"
                      ? "Verification revoked"
                      : current.status === "rejected"
                        ? "Not approved"
                        : "Verification expired"}
            </p>
          </div>
          {current.verifierNotes && (
            <p className="mt-2 text-xs text-slate-600 dark:text-slate-300">{current.verifierNotes}</p>
          )}
          <dl className="mt-3 space-y-1.5 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500 dark:text-slate-400">Evidence reference</dt>
              <dd className="font-semibold text-slate-700 dark:text-slate-200">{current.evidenceRef || "--"}</dd>
            </div>
            {current.expiresAt && (
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">{currentIsLiveVerified ? "Valid until" : "Expired"}</dt>
                <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatDate(current.expiresAt)}</dd>
              </div>
            )}
          </dl>
          <div className="mt-4">
            <Button size="sm" variant="outline" onClick={() => setShowForm(true)}>
              {currentIsLiveVerified ? "Submit again" : "Re-submit"}
            </Button>
          </div>
        </Card>
      )}

      {(!current || showForm) && (
        <DeclareForm
          roomId={roomId}
          onCancel={current ? () => setShowForm(false) : undefined}
          onSubmitted={(record) => {
            setShowForm(false);
            showToast(
              record.status === "verified"
                ? "Verified automatically -- the document matched this property's registered address."
                : record.status === "additional_evidence_required"
                  ? "Automated scan couldn't confirm this document -- see the note below."
                  : "Submitted for admin verification."
            );
            load();
          }}
        />
      )}

      <Toast toast={toast} />
    </div>
  );
}

function DeclareForm({
  roomId,
  onCancel,
  onSubmitted,
}: {
  roomId: number;
  onCancel?: () => void;
  onSubmitted: (record: PropertyVerification) => void;
}) {
  const [evidenceRef, setEvidenceRef] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!evidenceRef.trim()) {
      setError("Enter an evidence reference.");
      return;
    }
    if (!file) {
      setError("Upload an evidence document.");
      return;
    }
    setSubmitting(true);
    try {
      const record = await declareHostedPropertyVerification(roomId, { evidenceRef: evidenceRef.trim(), file });
      onSubmitted(record);
    } catch (err) {
      setError(errorMessage(err, "Could not submit property evidence."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {error && (
        <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
          {error}
        </p>
      )}

      <Field label="Evidence reference *" hint="e.g. title deed, utility bill, or lease reference">
        <input required value={evidenceRef} onChange={(e) => setEvidenceRef(e.target.value)} className={inputClass} />
      </Field>

      <div>
        <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Upload evidence document
        </span>
        <label className="flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed border-slate-200 bg-white px-4 py-4 text-sm text-slate-500 transition-colors hover:border-primary-300 hover:bg-primary-50/50 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
          <Upload className="h-5 w-5 shrink-0 text-slate-400" />
          <span className="min-w-0 flex-1 truncate">{file ? file.name : "Choose a PDF, JPG or PNG file"}</span>
          <input
            type="file"
            accept={ACCEPTED_DOCUMENT_EXTENSIONS}
            onChange={(e) => {
              const selected = e.target.files?.[0] ?? null;
              if (selected && selected.size > MAX_DOCUMENT_SIZE_MB * 1024 * 1024) {
                setFileError(`That file is larger than ${MAX_DOCUMENT_SIZE_MB}MB.`);
                setFile(null);
                e.target.value = "";
                return;
              }
              setFileError("");
              setFile(selected);
            }}
            className="hidden"
          />
        </label>
        <p className="mt-1.5 text-xs text-slate-400">PDF, JPG or PNG, up to {MAX_DOCUMENT_SIZE_MB}MB.</p>
        {fileError && <p className="mt-1 text-xs font-medium text-accent-600">{fileError}</p>}
      </div>

      <p className="flex items-start gap-1.5 text-xs text-slate-400">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> Document should clearly show the
        property&apos;s address.
      </p>

      <div className="flex justify-end gap-2 pt-1">
        {onCancel && (
          <Button type="button" variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
        <Button type="submit" loading={submitting}>
          Submit for verification
        </Button>
      </div>
    </form>
  );
}
