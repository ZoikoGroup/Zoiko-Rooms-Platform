"use client";

import { useEffect, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { BadgeCheck, Clock, FileText, Loader2, ShieldCheck, Upload, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { IdentityDocumentType, DocumentCategory } from "@/lib/types";
import { identityStatusLabel, identityStatusTone } from "@/lib/status";
import {
  ACCEPTED_DOCUMENT_EXTENSIONS,
  MAX_DOCUMENT_SIZE_MB,
  documentCategories,
  documentCategoryHint,
  documentCategoryLabel,
  documentTypeLabel,
  documentTypesByCategory,
} from "@/lib/identity-documents";
import { formatDate } from "@/lib/utils";
import { errorMessage, identityDocumentUrl, submitIdentityVerification } from "@/lib/user-api";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";

// Must match AUTO_VERIFY_PENDING_NOTE in backend/app/crud/identity_verification.py --
// a pending upload with this note is verified automatically ~10s after upload.
const AUTO_VERIFY_PENDING_NOTE = "Verifying automatically.";
const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 30000;

export function IdentityVerificationManager() {
  const { identityRecords, identityStatus, identityVerified, refreshIdentity, loading } = useUserSession();
  const { toast, showToast } = useToast();

  const [category, setCategory] = useState<DocumentCategory>("identity");
  const [documentType, setDocumentType] = useState<IdentityDocumentType>(documentTypesByCategory.identity[0].value);
  const [customDocumentName, setCustomDocumentName] = useState("");
  const [documentNumber, setDocumentNumber] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [verifyingId, setVerifyingId] = useState<number | null>(null);
  const verifyingSince = useRef(0);

  // While an upload is being auto-verified, re-fetch until it leaves "pending".
  useEffect(() => {
    if (verifyingId === null) return;
    const record = identityRecords.find((r) => r.id === verifyingId);
    if (record && record.status !== "pending") {
      setVerifyingId(null);
      if (record.status === "verified") showToast("Your identity is verified.");
      return;
    }
    if (Date.now() - verifyingSince.current > POLL_TIMEOUT_MS) {
      setVerifyingId(null);
      showToast("Still verifying — refresh the page in a moment.");
      return;
    }
    const timer = setTimeout(() => void refreshIdentity(), POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [verifyingId, identityRecords, refreshIdentity, showToast]);

  function handleCategoryChange(next: DocumentCategory) {
    setCategory(next);
    setDocumentType(documentTypesByCategory[next][0].value);
  }

  function handleFileChange(e: ChangeEvent<HTMLInputElement>) {
    const selected = e.target.files?.[0] ?? null;
    if (selected && selected.size > MAX_DOCUMENT_SIZE_MB * 1024 * 1024) {
      setError(`That file is larger than ${MAX_DOCUMENT_SIZE_MB}MB.`);
      setFile(null);
      e.target.value = "";
      return;
    }
    setError("");
    setFile(selected);
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) {
      setError("Upload a copy of the document — a PDF, JPG or PNG.");
      return;
    }
    if (documentType === "other" && !customDocumentName.trim()) {
      setError("Tell us what this document is.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const submitted = await submitIdentityVerification({
        documentType,
        file,
        documentNumber: documentNumber.trim(),
        customDocumentName: customDocumentName.trim(),
      });
      setDocumentNumber("");
      setCustomDocumentName("");
      setFile(null);
      await refreshIdentity();
      // Uploads are verified automatically after a few seconds; only a
      // duplicate document waits for a reviewer.
      if (submitted.status === "verified") {
        showToast("Your identity is verified.");
      } else if (submitted.verifierNotes === AUTO_VERIFY_PENDING_NOTE) {
        verifyingSince.current = Date.now();
        setVerifyingId(submitted.id);
      } else {
        showToast("Document submitted. A Zoiko reviewer will check it shortly.");
      }
    } catch (err) {
      setError(errorMessage(err, "Could not submit your document. Please try again."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card
        className={
          identityVerified
            ? "!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20"
            : undefined
        }
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-3">
            <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              {identityVerified ? (
                <BadgeCheck className="h-5 w-5" />
              ) : verifyingId !== null ? (
                <Loader2 className="h-5 w-5 animate-spin" />
              ) : (
                <ShieldCheck className="h-5 w-5" />
              )}
            </span>
            <div>
              <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                {loading
                  ? "Checking your verification status..."
                  : verifyingId !== null
                    ? "Verifying your identity..."
                    : identityStatusLabel[identityStatus]}
              </p>
              <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                {identityVerified
                  ? "You can submit rental applications and publish hosted listings."
                  : verifyingId !== null
                    ? "This usually takes 10–15 seconds."
                    : "Applying to rent a room and publishing a listing both require a verified identity."}
              </p>
            </div>
          </div>
          <Badge tone={identityStatusTone[identityStatus]} dot>
            {verifyingId !== null ? "Verifying" : identityStatusLabel[identityStatus]}
          </Badge>
        </div>
      </Card>

      <Card>
        <SectionHeading
          title="Submit a document"
          subtitle="Choose a category and document type, then upload a copy of the document."
        />

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          <div>
            <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Category
            </span>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
              {documentCategories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => handleCategoryChange(cat)}
                  title={documentCategoryHint[cat]}
                  className={`rounded-xl px-4 py-3 text-left text-sm font-semibold transition-all ${
                    category === cat
                      ? "bg-primary-700 text-white shadow-lg shadow-primary-900/25"
                      : "bg-slate-50 text-slate-600 ring-1 ring-slate-200 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700"
                  }`}
                >
                  {documentCategoryLabel[cat]}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-slate-400">{documentCategoryHint[category]}</p>
          </div>

          <Field label="Document type">
            <select
              value={documentType}
              onChange={(e) => setDocumentType(e.target.value as IdentityDocumentType)}
              className={inputClass}
            >
              {documentTypesByCategory[category].map((doc) => (
                <option key={doc.value} value={doc.value}>
                  {doc.label}
                </option>
              ))}
            </select>
          </Field>

          {documentType === "other" && (
            <Field label="Document name" hint="Tell us what this document is.">
              <input
                value={customDocumentName}
                onChange={(e) => setCustomDocumentName(e.target.value)}
                placeholder="e.g. Company employee ID badge"
                className={inputClass}
              />
            </Field>
          )}

          <Field label="Document number / reference (optional)">
            <input
              value={documentNumber}
              onChange={(e) => setDocumentNumber(e.target.value)}
              placeholder="e.g. the ID number printed on the document"
              className={inputClass}
            />
          </Field>

          <div>
            <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Upload document
            </span>
            <label className="flex cursor-pointer items-center gap-3 rounded-xl border-2 border-dashed border-slate-200 bg-slate-50 px-4 py-4 text-sm text-slate-500 transition-colors hover:border-primary-300 hover:bg-primary-50/50 dark:border-slate-700 dark:bg-slate-800/60 dark:text-slate-400">
              <Upload className="h-5 w-5 shrink-0 text-slate-400" />
              <span className="min-w-0 flex-1 truncate">
                {file ? file.name : "Choose a PDF, JPG or PNG file"}
              </span>
              <input
                type="file"
                accept={ACCEPTED_DOCUMENT_EXTENSIONS}
                onChange={handleFileChange}
                className="hidden"
              />
            </label>
            <p className="mt-1.5 text-xs text-slate-400">PDF, JPG or PNG, up to {MAX_DOCUMENT_SIZE_MB}MB.</p>
          </div>

          {error && (
            <p className="animate-fade-in rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {error}
            </p>
          )}

          <Button type="submit" loading={submitting} disabled={verifyingId !== null}>
            {submitting ? "Submitting" : "Submit for verification"}
          </Button>
        </form>
      </Card>

      <Card>
        <SectionHeading title="Submission history" subtitle="Every document you have submitted, newest first." />

        {identityRecords.length === 0 ? (
          <EmptyState message="You have not submitted an identity document yet." />
        ) : (
          <ul className="mt-4 space-y-3">
            {identityRecords.map((record) => (
              <li
                key={record.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                    {record.documentType === "other" && record.customDocumentName
                      ? record.customDocumentName
                      : documentTypeLabel[record.documentType] ?? record.documentType}
                    <span className="ml-2 text-xs font-normal text-slate-400">
                      {documentCategoryLabel[record.documentCategory] ?? record.documentCategory}
                    </span>
                  </p>
                  <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" /> Submitted {formatDate(record.createdAt)}
                    </span>
                    {record.verifiedAt && (
                      <span className="flex items-center gap-1">
                        <BadgeCheck className="h-3 w-3" /> Verified {formatDate(record.verifiedAt)}
                      </span>
                    )}
                    {record.expiresAt && <span>Expires {formatDate(record.expiresAt)}</span>}
                    {record.hasDocument && (
                      <a
                        href={identityDocumentUrl(record.id)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300"
                      >
                        <FileText className="h-3 w-3" /> View my document
                      </a>
                    )}
                  </p>
                  {record.status === "rejected" && (
                    <p className="mt-1.5 flex items-start gap-1 text-xs text-accent-600">
                      <XCircle className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>
                        {record.verifierNotes
                          ? `Rejected: ${record.verifierNotes}`
                          : "Rejected. Submit a new document to try again."}
                      </span>
                    </p>
                  )}
                  {record.status === "additional_evidence_required" && (
                    <p className="mt-1.5 flex items-start gap-1 text-xs text-amber-600">
                      <Clock className="mt-0.5 h-3 w-3 shrink-0" />
                      <span>
                        {record.verifierNotes
                          ? `More evidence needed: ${record.verifierNotes}`
                          : "We need additional or different evidence before we can verify you."}
                        {" "}Submit a new document above to continue.
                      </span>
                    </p>
                  )}
                </div>
                <Badge tone={identityStatusTone[record.status] ?? "neutral"}>
                  {identityStatusLabel[record.status] ?? record.status}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Toast toast={toast} />
    </div>
  );
}
