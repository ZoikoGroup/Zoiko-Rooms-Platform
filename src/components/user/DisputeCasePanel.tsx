"use client";

import { useEffect, useState } from "react";
import { Download, FileWarning, Gavel, Plus, Scale } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import {
  DisputeCaseExportRead,
  DisputeCaseMessageRead,
  DisputeCaseRead,
  DisputeClaimFamily,
  DisputeClaimRead,
  DisputeDeadlineRead,
  DisputeEvidenceRead,
  DisputeExternalProceedingRead,
  DisputePartyRead,
  DisputeSettlementRead,
  PaymentDisputePartyAction,
} from "@/lib/types";
import {
  disputeCaseStatusTone,
  disputeClaimFamilyLabel,
  disputeClaimStatusTone,
  disputeDeadlineStatusTone,
  disputeExternalProceedingStatusTone,
  disputeSettlementStatusTone,
  disputeSeverityTone,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import { errorMessage, renterDisputes } from "@/lib/user-api";
import { Card, EmptyState, Field, inputClass, selectClass } from "@/components/user/ui";
import type { ToastTone } from "@/components/user/ui";

/** ZR-ENG-CLR-010: this panel is shared by the renter side (embedded per
 *  occupancy card in RentalsManager.tsx) and the host side
 *  (HostDisputesManager.tsx, a flat page-level list) -- the backend routers
 *  really are byte-for-byte mirrors of each other, so one implementation
 *  parameterised by `client` + `viewerRole` covers both instead of
 *  hand-duplicating ~500 lines of near-identical detail-view UI twice. */

export type DisputeViewerRole = "RENTER" | "HOST";

const PAYMENT_CLAIM_SOURCE = "RENTAL_PAYMENT_DISPUTE";

function isPaymentClaim(claim: DisputeClaimRead): boolean {
  return claim.sourceRecordType === PAYMENT_CLAIM_SOURCE && Boolean(claim.sourceRecordId);
}

/** The actions each side has on a payment-record problem. Each side can only
 *  concede its own point, so neither decides it in their own favour; the
 *  backend checks the same rules. */
function paymentActionsFor(
  viewerRole: DisputeViewerRole,
  caseData: DisputeCaseRead,
): { action: PaymentDisputePartyAction; label: string; hint: string }[] {
  const reportedByViewer = viewerRole === "RENTER" ? Boolean(caseData.openedByGuestId) : Boolean(caseData.openedByPartyId);
  const actions: { action: PaymentDisputePartyAction; label: string; hint: string }[] =
    viewerRole === "HOST"
      ? [{ action: "CONFIRM_RECEIVED", label: "I've received this payment", hint: "The payment is confirmed and this dispute closes." }]
      : [{ action: "CONFIRM_NOT_PAID", label: "This payment wasn't made", hint: "The amount becomes due again and this dispute closes." }];
  if (reportedByViewer) {
    actions.push({ action: "WITHDRAW", label: "Withdraw my report", hint: "The payment is left as it is and this dispute closes." });
  }
  return actions;
}
/** Both `renterDisputes` and `hostDisputes` come from the same factory in
 *  user-api.ts, so they share this exact shape. */
export type DisputeClient = typeof renterDisputes;

export const DISPUTE_CLAIM_FAMILIES: DisputeClaimFamily[] = [
  "DEPOSIT",
  "PAYMENT",
  "REFUND_PAYOUT",
  "PROPERTY_CONDITION",
  "BOOKING_AGREEMENT",
  "SUBLET_OCCUPANCY",
  "MARKETPLACE_CONDUCT",
  "PROTECTED_SAFETY",
  "VERIFICATION_FRAUD",
  "ZOIKO_SERVICE",
];

const TERMINAL_CLAIM_STATUSES = new Set(["UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED", "WITHDRAWN"]);
/** Host<->renter claims the two sides settle themselves (A0/A6 stay with Zoiko Rooms). */
const PARTY_RESOLVED_AUTHORITIES = new Set(["A1", "A2"]);
const ANSWERABLE_CLAIM_STATUSES = new Set(["OPEN", "RESPONSE_DUE", "EVIDENCE"]);
const OUTSIDE_BODIES: { value: string; label: string }[] = [
  { value: "DEPOSIT_SCHEME", label: "Deposit protection scheme" },
  { value: "TRIBUNAL_COURT", label: "Tribunal or court" },
  { value: "MEDIATION_ADR", label: "Mediation" },
  { value: "OTHER", label: "Other" },
];
const OUTSIDE_OUTCOMES: { value: string; label: string }[] = [
  { value: "UPHELD", label: "Claim upheld" },
  { value: "PARTLY_UPHELD", label: "Partly upheld" },
  { value: "NOT_UPHELD", label: "Not upheld" },
  { value: "SETTLED", label: "Settled" },
];
const TERMINAL_SETTLEMENT_STATUSES = new Set(["ACCEPTED", "REJECTED", "EXPIRED", "EFFECTIVE", "VOID"]);

function parseOptionalNumber(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function label(text: string): string {
  return text.replace(/_/g, " ");
}

// -- New-case form ------------------------------------------------------

function NewClaimFields({
  claimCode,
  setClaimCode,
  claimFamily,
  setClaimFamily,
  amount,
  setAmount,
  currency,
  setCurrency,
  remedy,
  setRemedy,
  safetyFlag,
  setSafetyFlag,
}: {
  claimCode: string;
  setClaimCode: (v: string) => void;
  claimFamily: DisputeClaimFamily;
  setClaimFamily: (v: DisputeClaimFamily) => void;
  amount: string;
  setAmount: (v: string) => void;
  currency: string;
  setCurrency: (v: string) => void;
  remedy: string;
  setRemedy: (v: string) => void;
  safetyFlag: boolean;
  setSafetyFlag: (v: boolean) => void;
}) {
  return (
    <>
      <Field label="Claim family">
        <select value={claimFamily} onChange={(e) => setClaimFamily(e.target.value as DisputeClaimFamily)} className={selectClass}>
          {DISPUTE_CLAIM_FAMILIES.map((family) => (
            <option key={family} value={family}>
              {disputeClaimFamilyLabel[family] ?? family}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Claim code" hint="A short code identifying what this claim is about, e.g. DEPOSIT_NOT_RETURNED.">
        <input type="text" value={claimCode} onChange={(e) => setClaimCode(e.target.value)} className={inputClass} />
      </Field>
      <Field label="Requested remedy">
        <textarea value={remedy} onChange={(e) => setRemedy(e.target.value)} rows={2} className={inputClass} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Amount (optional)">
          <input type="number" min={0} step="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} className={inputClass} />
        </Field>
        <Field label="Currency">
          <input type="text" value={currency} onChange={(e) => setCurrency(e.target.value.toUpperCase())} maxLength={3} className={inputClass} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
        <input type="checkbox" checked={safetyFlag} onChange={(e) => setSafetyFlag(e.target.checked)} />
        This involves a safety concern
      </label>
    </>
  );
}

function OpenCaseModal({
  open,
  onClose,
  client,
  occupancyId,
  allowOccupancyInput,
  defaultCurrency,
  showToast,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  client: DisputeClient;
  occupancyId?: number;
  allowOccupancyInput?: boolean;
  defaultCurrency: string;
  showToast: (message: string, tone?: ToastTone) => void;
  onCreated: () => void;
}) {
  const [occupancyInput, setOccupancyInput] = useState("");
  const [claimCode, setClaimCode] = useState("");
  const [claimFamily, setClaimFamily] = useState<DisputeClaimFamily>("DEPOSIT");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState(defaultCurrency);
  const [remedy, setRemedy] = useState("");
  const [safetyFlag, setSafetyFlag] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setOccupancyInput("");
    setClaimCode("");
    setClaimFamily("DEPOSIT");
    setAmount("");
    setCurrency(defaultCurrency);
    setRemedy("");
    setSafetyFlag(false);
    setError("");
  }, [open, defaultCurrency]);

  async function handleSubmit() {
    const resolvedOccupancyId = allowOccupancyInput ? parseOptionalNumber(occupancyInput) : occupancyId;
    if (allowOccupancyInput && !resolvedOccupancyId) {
      setError("Enter the occupancy ID of the tenancy this dispute is about (a whole number, e.g. 12).");
      return;
    }
    if (!claimCode.trim()) {
      setError("Enter a claim code — a short code for what this is about, e.g. DEPOSIT_NOT_RETURNED.");
      return;
    }
    if (amount.trim() && parseOptionalNumber(amount) === undefined) {
      setError("Enter the amount as a number, e.g. 250.00 — or leave it empty.");
      return;
    }
    if (currency.trim() && currency.trim().length !== 3) {
      setError("Enter the currency as a 3-letter code, e.g. GBP.");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      await client.openCase({
        occupancyId: resolvedOccupancyId ?? null,
        claim: {
          claimCode: claimCode.trim(),
          claimFamily,
          amount: parseOptionalNumber(amount) ?? null,
          currency: currency.trim() || undefined,
          requestedRemedy: remedy.trim(),
          safetyFlag,
        },
      });
      showToast("Dispute case opened.");
      onCreated();
      onClose();
    } catch (err) {
      setError(errorMessage(err, "Could not open this dispute."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Open a Dispute">
      <div className="space-y-3.5">
        <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          Opening a dispute starts a formal review of a claim tied to this tenancy. You can add more claims and
          evidence after it&apos;s opened.
        </p>
        {allowOccupancyInput && (
          <Field label="Occupancy ID" hint="The numeric ID of the tenancy this dispute relates to -- you can find it on the tenant's rental record.">
            <input type="number" min={1} value={occupancyInput} onChange={(e) => setOccupancyInput(e.target.value)} className={inputClass} />
          </Field>
        )}
        <NewClaimFields
          claimCode={claimCode}
          setClaimCode={setClaimCode}
          claimFamily={claimFamily}
          setClaimFamily={setClaimFamily}
          amount={amount}
          setAmount={setAmount}
          currency={currency}
          setCurrency={setCurrency}
          remedy={remedy}
          setRemedy={setRemedy}
          safetyFlag={safetyFlag}
          setSafetyFlag={setSafetyFlag}
        />
        {error && (
          <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">{error}</p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" variant="primary" loading={submitting} onClick={handleSubmit}>
            Open dispute
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// -- Export modal ---------------------------------------------------------

function ExportModal({
  open,
  onClose,
  client,
  caseId,
}: {
  open: boolean;
  onClose: () => void;
  client: DisputeClient;
  caseId: number | null;
}) {
  const [data, setData] = useState<DisputeCaseExportRead | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open || !caseId) return;
    setLoading(true);
    setError("");
    client
      .getCaseExport(caseId)
      .then(setData)
      .catch((err) => setError(errorMessage(err, "Could not load this export.")))
      .finally(() => setLoading(false));
  }, [open, caseId, client]);

  return (
    <Modal open={open} onClose={onClose} title={`Case #${caseId ?? ""} Export`} size="lg">
      {loading ? (
        <Loader label="Loading export" />
      ) : error ? (
        <p className="text-xs font-medium text-accent-700">{error}</p>
      ) : data ? (
        <div className="space-y-4">
          <p className="text-xs text-slate-400">{data.note}</p>
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Chronology</p>
            <div className="max-h-64 space-y-1.5 overflow-y-auto rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              {data.chronology.length === 0 ? (
                <p className="text-xs text-slate-400">No recorded events.</p>
              ) : (
                data.chronology.map((event, i) => (
                  <div key={i} className="text-xs text-slate-600 dark:text-slate-300">
                    <span className="font-mono text-slate-400">{formatDate(event.timestamp)}</span> — <span className="font-semibold">{label(event.eventType)}</span>
                    {event.summary ? `: ${event.summary}` : ""}
                  </div>
                ))
              )}
            </div>
          </div>
          <div>
            <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Evidence index</p>
            <div className="max-h-48 space-y-1.5 overflow-y-auto rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              {data.evidenceIndex.length === 0 ? (
                <p className="text-xs text-slate-400">No evidence on file.</p>
              ) : (
                data.evidenceIndex.map((item) => (
                  <div key={item.id} className="text-xs text-slate-600 dark:text-slate-300">
                    {item.originalFilename} · {(item.sizeBytes / 1024).toFixed(1)} KB · {label(item.verificationStatus)}
                  </div>
                ))
              )}
            </div>
          </div>
          <p className="text-xs text-slate-400">Generated {formatDate(data.generatedAt)}</p>
        </div>
      ) : null}
    </Modal>
  );
}

// -- Case detail modal ------------------------------------------------------

function DisputeCaseDetail({
  open,
  onClose,
  client,
  viewerRole,
  caseId,
  defaultCurrency,
  showToast,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  client: DisputeClient;
  viewerRole: DisputeViewerRole;
  caseId: number;
  /** The case's own currency (from its claims); prefills the money fields. */
  defaultCurrency: string;
  showToast: (message: string, tone?: ToastTone) => void;
  onChanged: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [caseData, setCaseData] = useState<DisputeCaseRead | null>(null);
  const [evidence, setEvidence] = useState<DisputeEvidenceRead[]>([]);
  const [messages, setMessages] = useState<DisputeCaseMessageRead[]>([]);
  const [settlements, setSettlements] = useState<DisputeSettlementRead[]>([]);
  const [deadlines, setDeadlines] = useState<DisputeDeadlineRead[]>([]);
  const [parties, setParties] = useState<DisputePartyRead[]>([]);
  const [external, setExternal] = useState<DisputeExternalProceedingRead[]>([]);

  // Answering a claim / the outside body (host<->renter claims, no admin)
  const [answerBusy, setAnswerBusy] = useState<number | null>(null);
  const [contestClaimId, setContestClaimId] = useState<number | null>(null);
  const [contestNote, setContestNote] = useState("");
  const [fileClaimId, setFileClaimId] = useState<number | null>(null);
  const [fileBody, setFileBody] = useState("DEPOSIT_SCHEME");
  const [fileName, setFileName] = useState("");
  const [fileRef, setFileRef] = useState("");
  const [decisionFor, setDecisionFor] = useState<number | null>(null);
  const [decisionOutcome, setDecisionOutcome] = useState("UPHELD");
  const [decisionDate, setDecisionDate] = useState("");
  const [decisionFinal, setDecisionFinal] = useState(true);
  const [outsideBusy, setOutsideBusy] = useState(false);

  // Payment-record problem (resolved between tenant and host)
  const [paymentConfirm, setPaymentConfirm] = useState<{ claimId: number; action: PaymentDisputePartyAction } | null>(null);
  const [paymentNotes, setPaymentNotes] = useState("");
  const [paymentBusy, setPaymentBusy] = useState(false);

  // Add claim
  const [addClaimOpen, setAddClaimOpen] = useState(false);
  const [newClaimCode, setNewClaimCode] = useState("");
  const [newClaimFamily, setNewClaimFamily] = useState<DisputeClaimFamily>("DEPOSIT");
  const [newClaimAmount, setNewClaimAmount] = useState("");
  const [newClaimCurrency, setNewClaimCurrency] = useState(defaultCurrency);
  const [newClaimRemedy, setNewClaimRemedy] = useState("");
  const [newClaimSafetyFlag, setNewClaimSafetyFlag] = useState(false);
  const [claimBusy, setClaimBusy] = useState(false);

  // Request review
  const [reviewClaimId, setReviewClaimId] = useState<number | null>(null);
  const [reviewReason, setReviewReason] = useState("");

  // Evidence upload
  const [evidenceFile, setEvidenceFile] = useState<File | null>(null);
  const [evidenceNote, setEvidenceNote] = useState("");
  const [evidenceClaimIds, setEvidenceClaimIds] = useState<number[]>([]);
  const [evidenceBusy, setEvidenceBusy] = useState(false);

  // Messages
  const [messageDraft, setMessageDraft] = useState("");
  const [messageBusy, setMessageBusy] = useState(false);

  // Propose settlement
  const [settlementFormOpen, setSettlementFormOpen] = useState(false);
  const [settlementClaimIds, setSettlementClaimIds] = useState<number[]>([]);
  const [settlementTerms, setSettlementTerms] = useState("");
  const [settlementAmount, setSettlementAmount] = useState("");
  const [settlementCurrency, setSettlementCurrency] = useState(defaultCurrency);
  const [settlementExpiresAt, setSettlementExpiresAt] = useState("");
  const [settlementAck, setSettlementAck] = useState(false);
  const [settlementBusy, setSettlementBusy] = useState(false);

  // Counter/respond to a settlement
  const [counterTargetId, setCounterTargetId] = useState<number | null>(null);
  const [counterTerms, setCounterTerms] = useState("");
  const [counterAmount, setCounterAmount] = useState("");
  const [counterCurrency, setCounterCurrency] = useState(defaultCurrency);
  const [counterExpiresAt, setCounterExpiresAt] = useState("");
  const [settlementActionBusy, setSettlementActionBusy] = useState<number | null>(null);

  async function loadAll() {
    setLoading(true);
    setLoadError("");
    try {
      const [caseRead, evidenceList, messageList, settlementList, deadlineList, partyList, externalList] = await Promise.all([
        client.getCase(caseId),
        client.listEvidence(caseId),
        client.listMessages(caseId),
        client.listSettlements(caseId),
        client.listDeadlines(caseId),
        client.listParties(caseId),
        client.listExternalProceedings(caseId),
      ]);
      setCaseData(caseRead);
      setEvidence(evidenceList);
      setMessages(messageList);
      setSettlements(settlementList);
      setDeadlines(deadlineList);
      setParties(partyList);
      setExternal(externalList);
    } catch (err) {
      setLoadError(errorMessage(err, "Could not load this case."));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    loadAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, caseId]);

  function toggleClaimId(list: number[], id: number, setList: (v: number[]) => void) {
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id]);
  }

  // The person who opened a finished dispute deletes it -- no admin review.
  const [confirmRemoveCase, setConfirmRemoveCase] = useState(false);
  const [removeBusy, setRemoveBusy] = useState(false);
  async function handleRemoveCase() {
    setRemoveBusy(true);
    try {
      await client.removeCase(caseId);
      showToast("Dispute deleted.");
      onClose();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not delete this dispute."), "error");
    } finally {
      setRemoveBusy(false);
    }
  }

  async function handleAddClaim() {
    if (!newClaimCode.trim()) {
      showToast("Enter a claim code — a short code for what this claim is about, e.g. DEPOSIT_NOT_RETURNED.", "error");
      return;
    }
    if (newClaimAmount.trim() && parseOptionalNumber(newClaimAmount) === undefined) {
      showToast("Enter the amount as a number, e.g. 250.00 — or leave it empty.", "error");
      return;
    }
    setClaimBusy(true);
    try {
      await client.addClaim(caseId, {
        claimCode: newClaimCode.trim(),
        claimFamily: newClaimFamily,
        amount: parseOptionalNumber(newClaimAmount) ?? null,
        currency: newClaimCurrency,
        requestedRemedy: newClaimRemedy.trim(),
        safetyFlag: newClaimSafetyFlag,
      });
      setNewClaimCode("");
      setNewClaimAmount("");
      setNewClaimRemedy("");
      setNewClaimSafetyFlag(false);
      setAddClaimOpen(false);
      showToast("Claim added.");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not add this claim."), "error");
    } finally {
      setClaimBusy(false);
    }
  }

  async function handleRequestReview(claimId: number) {
    if (!reviewReason.trim()) {
      showToast("Tell us why you want this claim reviewed.", "error");
      return;
    }
    setClaimBusy(true);
    try {
      await client.requestClaimReview(caseId, claimId, reviewReason.trim());
      setReviewClaimId(null);
      setReviewReason("");
      showToast("Review requested.");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not request a review."), "error");
    } finally {
      setClaimBusy(false);
    }
  }

  async function handleUploadEvidence() {
    if (!evidenceFile && !evidenceNote.trim()) {
      showToast("Choose a file to upload or write a note — evidence can't be empty.", "error");
      return;
    }
    setEvidenceBusy(true);
    try {
      await client.uploadEvidence(caseId, {
        file: evidenceFile,
        noteText: evidenceNote.trim(),
        claimIds: evidenceClaimIds,
      });
      setEvidenceFile(null);
      setEvidenceNote("");
      setEvidenceClaimIds([]);
      showToast("Evidence uploaded.");
      await loadAll();
    } catch (err) {
      showToast(errorMessage(err, "Could not upload this evidence."), "error");
    } finally {
      setEvidenceBusy(false);
    }
  }

  // Deleting your own evidence is immediate -- no admin review (only a legal
  // hold blocks it). The row stays on the case as "Deleted" so the history
  // isn't broken; the file and note are erased.
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  async function handleDeleteEvidence(evidenceId: number) {
    setEvidenceBusy(true);
    try {
      await client.requestEvidenceDeletion(caseId, evidenceId);
      showToast("Evidence deleted.");
      setConfirmDeleteId(null);
      await loadAll();
    } catch (err) {
      showToast(errorMessage(err, "Could not delete this evidence."), "error");
    } finally {
      setEvidenceBusy(false);
    }
  }

  async function handleSendMessage() {
    if (!messageDraft.trim()) {
      showToast("Type a message before sending.", "error");
      return;
    }
    setMessageBusy(true);
    try {
      await client.postMessage(caseId, messageDraft.trim());
      setMessageDraft("");
      await loadAll();
    } catch (err) {
      showToast(errorMessage(err, "Could not send this message."), "error");
    } finally {
      setMessageBusy(false);
    }
  }

  async function handleProposeSettlement() {
    const missing: string[] = [];
    if (settlementClaimIds.length === 0) missing.push("choose at least one claim this settlement covers");
    if (!settlementTerms.trim()) missing.push("write the settlement terms");
    if (settlementAmount.trim() && parseOptionalNumber(settlementAmount) === undefined) missing.push("enter the amount as a number");
    if (!settlementAck) missing.push("tick the box confirming this doesn't waive any legal right");
    if (missing.length > 0) {
      showToast(`To send this proposal: ${missing.join("; ")}.`, "error");
      return;
    }
    setSettlementBusy(true);
    try {
      await client.proposeSettlement(caseId, {
        claimIds: settlementClaimIds,
        termsText: settlementTerms.trim(),
        amount: parseOptionalNumber(settlementAmount) ?? null,
        currency: settlementCurrency,
        expiresAt: settlementExpiresAt ? new Date(settlementExpiresAt).toISOString() : null,
        acknowledgesNoNonwaivableWaiver: settlementAck,
      });
      setSettlementFormOpen(false);
      setSettlementClaimIds([]);
      setSettlementTerms("");
      setSettlementAmount("");
      setSettlementExpiresAt("");
      setSettlementAck(false);
      showToast("Settlement proposed.");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not propose this settlement."), "error");
    } finally {
      setSettlementBusy(false);
    }
  }

  async function handleRespondSettlement(settlementId: number, action: "ACCEPT" | "REJECT") {
    setSettlementActionBusy(settlementId);
    try {
      await client.respondSettlement(caseId, settlementId, { action });
      showToast(action === "ACCEPT" ? "Settlement accepted." : "Settlement rejected.");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not respond to this settlement."), "error");
    } finally {
      setSettlementActionBusy(null);
    }
  }

  async function handleCounterSettlement() {
    if (!counterTargetId) return;
    if (!counterTerms.trim()) {
      showToast("Write your counter-offer terms before sending.", "error");
      return;
    }
    if (counterAmount.trim() && parseOptionalNumber(counterAmount) === undefined) {
      showToast("Enter the counter amount as a number, e.g. 250.00 — or leave it empty.", "error");
      return;
    }
    setSettlementActionBusy(counterTargetId);
    try {
      await client.respondSettlement(caseId, counterTargetId, {
        action: "COUNTER",
        counterTermsText: counterTerms.trim(),
        counterAmount: parseOptionalNumber(counterAmount) ?? null,
        counterCurrency: counterCurrency,
        counterExpiresAt: counterExpiresAt ? new Date(counterExpiresAt).toISOString() : null,
      });
      setCounterTargetId(null);
      setCounterTerms("");
      setCounterAmount("");
      setCounterExpiresAt("");
      showToast("Counter-offer sent.");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not send this counter-offer."), "error");
    } finally {
      setSettlementActionBusy(null);
    }
  }

  async function handlePaymentResolution() {
    if (!paymentConfirm) {
      showToast("Choose how you want to resolve this payment problem first.", "error");
      return;
    }
    setPaymentBusy(true);
    try {
      await client.resolvePaymentClaim(caseId, paymentConfirm.claimId, paymentConfirm.action, paymentNotes.trim());
      showToast("Done -- the payment record is updated and this dispute is closed.");
      setPaymentConfirm(null);
      setPaymentNotes("");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not resolve this payment problem."), "error");
    } finally {
      setPaymentBusy(false);
    }
  }

  async function handleAnswerClaim(claimId: number, response: "ACCEPT" | "CONTEST") {
    setAnswerBusy(claimId);
    try {
      await client.respondToClaim(caseId, claimId, response, response === "CONTEST" ? contestNote.trim() : "");
      showToast(response === "ACCEPT" ? "Claim accepted." : "Claim contested -- try to agree a settlement before the deadline.");
      setContestClaimId(null);
      setContestNote("");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not answer this claim."), "error");
    } finally {
      setAnswerBusy(null);
    }
  }

  async function handleFileOutside() {
    if (fileClaimId == null) return;
    if (!fileName.trim() || !fileRef.trim()) {
      showToast("Enter the body's name and your case reference.", "error");
      return;
    }
    setOutsideBusy(true);
    try {
      await client.fileExternalProceeding(caseId, {
        claimIds: [fileClaimId], authorityType: fileBody, authorityName: fileName.trim(), externalReference: fileRef.trim(),
      });
      showToast("Recorded. Come back and record the decision once you have it.");
      setFileClaimId(null);
      setFileName("");
      setFileRef("");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not record this."), "error");
    } finally {
      setOutsideBusy(false);
    }
  }

  async function handleRecordDecision() {
    if (decisionFor == null) return;
    if (!decisionDate) {
      showToast("Enter the decision date.", "error");
      return;
    }
    setOutsideBusy(true);
    try {
      await client.recordExternalDecision(caseId, decisionFor, {
        outcome: decisionOutcome, decisionDate, finalityState: decisionFinal ? "FINAL" : "UNDER_REVIEW",
      });
      showToast("Decision recorded and applied.");
      setDecisionFor(null);
      setDecisionDate("");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not record the decision."), "error");
    } finally {
      setOutsideBusy(false);
    }
  }

  async function handleVoidSettlement(settlementId: number) {
    setSettlementActionBusy(settlementId);
    try {
      await client.voidSettlement(caseId, settlementId);
      showToast("Settlement voided.");
      await loadAll();
      onChanged();
    } catch (err) {
      showToast(errorMessage(err, "Could not void this settlement."), "error");
    } finally {
      setSettlementActionBusy(null);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title={`Case #${caseId}`} size="xl">
      {loading ? (
        <Loader label="Loading case" />
      ) : loadError ? (
        <p className="text-xs font-medium text-accent-700">{loadError}</p>
      ) : caseData ? (
        <div className="max-h-[70vh] space-y-5 overflow-y-auto pr-1">
          {/* Header */}
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={disputeSeverityTone[caseData.severity] ?? "neutral"}>{caseData.severity}</Badge>
              <Badge tone={disputeCaseStatusTone[caseData.status] ?? "neutral"}>{label(caseData.status)}</Badge>
              {caseData.assignedTeam && <Badge tone="neutral">{label(caseData.assignedTeam)}</Badge>}
              <span className="text-xs text-slate-400">Opened {formatDate(caseData.openedAt)}</span>
            </div>
            {/* Only the person who opened a finished dispute can delete it. */}
            {(viewerRole === "RENTER" ? Boolean(caseData.openedByGuestId) : Boolean(caseData.openedByPartyId)) &&
              (caseData.status === "RESOLVED" || caseData.status === "CLOSED") && (
                <div className="flex flex-wrap items-center gap-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                  {!confirmRemoveCase ? (
                    <>
                      <span>This dispute is finished. You can delete it from your account.</span>
                      <Button size="sm" variant="outline" onClick={() => setConfirmRemoveCase(true)}>
                        Delete this dispute
                      </Button>
                    </>
                  ) : (
                    <>
                      <span>
                        Delete this dispute? It disappears from your list and the evidence and messages you added are erased.
                        The other side keeps their copy, and money records are not changed.
                      </span>
                      <Button size="sm" variant="primary" loading={removeBusy} onClick={handleRemoveCase}>
                        Yes, delete it
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setConfirmRemoveCase(false)}>
                        Cancel
                      </Button>
                    </>
                  )}
                </div>
              )}
            {caseData.moneyStatus.length > 0 && (
              <div className="space-y-1 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                {caseData.moneyStatus.map((m) => (
                  <div key={m.currency} className="flex flex-wrap items-center justify-between gap-x-4">
                    <span className="font-semibold">{m.currency}</span>
                    <span>Disputed {formatCurrency(m.amountDisputed, m.currency)}</span>
                    <span>Held {formatCurrency(m.amountHeld, m.currency)}</span>
                    <span>Settled {formatCurrency(m.amountSettled, m.currency)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Claims */}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Claims</p>
              <Button size="sm" variant="ghost" onClick={() => setAddClaimOpen((v) => !v)}>
                <Plus className="h-3.5 w-3.5" /> Add claim
              </Button>
            </div>
            {addClaimOpen && (
              <div className="space-y-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                <NewClaimFields
                  claimCode={newClaimCode}
                  setClaimCode={setNewClaimCode}
                  claimFamily={newClaimFamily}
                  setClaimFamily={setNewClaimFamily}
                  amount={newClaimAmount}
                  setAmount={setNewClaimAmount}
                  currency={newClaimCurrency}
                  setCurrency={setNewClaimCurrency}
                  remedy={newClaimRemedy}
                  setRemedy={setNewClaimRemedy}
                  safetyFlag={newClaimSafetyFlag}
                  setSafetyFlag={setNewClaimSafetyFlag}
                />
                <Button size="sm" variant="primary" fullWidth loading={claimBusy} onClick={handleAddClaim}>
                  Submit claim
                </Button>
              </div>
            )}
            <div className="space-y-2">
              {caseData.claims.map((claim: DisputeClaimRead) => (
                <div key={claim.id} className="space-y-1.5 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                  <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600 dark:text-slate-300">
                    <span>
                      <span className="font-semibold">{claim.claimCode}</span> · {disputeClaimFamilyLabel[claim.claimFamily] ?? claim.claimFamily}
                    </span>
                    <span className="flex items-center gap-2">
                      {claim.amount != null && <span>{formatCurrency(claim.amount, claim.currency)}</span>}
                      {claim.outcome && <span className="italic">{label(claim.outcome)}</span>}
                      <Badge tone={disputeClaimStatusTone[claim.status] ?? "neutral"}>{label(claim.status)}</Badge>
                    </span>
                  </div>
                  {claim.requestedRemedy && <p className="text-xs text-slate-500 dark:text-slate-400">&ldquo;{claim.requestedRemedy}&rdquo;</p>}
                  {claim.authorityClass && PARTY_RESOLVED_AUTHORITIES.has(claim.authorityClass) && ANSWERABLE_CLAIM_STATUSES.has(claim.status) && (
                    claim.claimantRole !== viewerRole ? (
                      <div className="space-y-2 border-t border-slate-200 pt-2 dark:border-slate-700">
                        <p className="text-xs text-slate-600 dark:text-slate-300">
                          Answer this claim. If you don&apos;t answer before the response deadline, it counts as accepted.
                        </p>
                        {contestClaimId === claim.id ? (
                          <>
                            <textarea
                              className={inputClass}
                              rows={2}
                              placeholder="Why you disagree (optional)"
                              value={contestNote}
                              onChange={(e) => setContestNote(e.target.value)}
                            />
                            <div className="flex gap-2">
                              <Button size="sm" variant="primary" loading={answerBusy === claim.id} onClick={() => handleAnswerClaim(claim.id, "CONTEST")}>
                                Contest claim
                              </Button>
                              <Button size="sm" variant="ghost" onClick={() => setContestClaimId(null)}>Cancel</Button>
                            </div>
                          </>
                        ) : (
                          <div className="flex gap-2">
                            <Button size="sm" variant="outline" loading={answerBusy === claim.id} onClick={() => handleAnswerClaim(claim.id, "ACCEPT")}>
                              Accept
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => { setContestClaimId(claim.id); setContestNote(""); }}>
                              Contest
                            </Button>
                          </div>
                        )}
                      </div>
                    ) : (
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        Waiting for the other side to answer. If they don&apos;t answer by the deadline, your claim is accepted.
                      </p>
                    )
                  )}
                  {claim.authorityClass && PARTY_RESOLVED_AUTHORITIES.has(claim.authorityClass) && claim.status === "NEGOTIATION" && (
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Contested. Try to agree a settlement below; if you can&apos;t before the deadline, it goes to the outside body.
                    </p>
                  )}
                  {claim.authorityClass && PARTY_RESOLVED_AUTHORITIES.has(claim.authorityClass) && claim.status === "EXTERNAL_REFERRAL"
                    && !external.some((e) => e.claimIds.includes(claim.id)) && (
                    <div className="space-y-2 border-t border-slate-200 pt-2 dark:border-slate-700">
                      <p className="text-xs text-slate-600 dark:text-slate-300">
                        No settlement was reached. Take this to the body that handles it (for a deposit, the deposit protection scheme), then record it here.
                      </p>
                      {fileClaimId === claim.id ? (
                        <>
                          <select className={selectClass} value={fileBody} onChange={(e) => setFileBody(e.target.value)}>
                            {OUTSIDE_BODIES.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
                          </select>
                          <input className={inputClass} placeholder="Name of the body" value={fileName} onChange={(e) => setFileName(e.target.value)} />
                          <input className={inputClass} placeholder="Your case reference" value={fileRef} onChange={(e) => setFileRef(e.target.value)} />
                          <div className="flex gap-2">
                            <Button size="sm" variant="primary" loading={outsideBusy} onClick={handleFileOutside}>Save</Button>
                            <Button size="sm" variant="ghost" onClick={() => setFileClaimId(null)}>Cancel</Button>
                          </div>
                        </>
                      ) : (
                        <Button size="sm" variant="outline" onClick={() => setFileClaimId(claim.id)}>
                          <Gavel className="h-3.5 w-3.5" /> I&apos;ve taken it to an outside body
                        </Button>
                      )}
                    </div>
                  )}
                  {isPaymentClaim(claim) && !TERMINAL_CLAIM_STATUSES.has(claim.status) && (
                    <div className="space-y-2 pt-1">
                      <p className="text-xs text-slate-500 dark:text-slate-400">
                        A problem reported on a payment. You and the other side sort this out between you -- once either of
                        you confirms what happened, the payment record updates and this dispute closes.
                      </p>
                      {paymentConfirm?.claimId === claim.id ? (
                        <div className="space-y-2 rounded-lg bg-white p-3 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
                          <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">
                            {paymentActionsFor(viewerRole, caseData).find((a) => a.action === paymentConfirm.action)?.label}
                          </p>
                          <p className="text-xs text-slate-500 dark:text-slate-400">
                            {paymentActionsFor(viewerRole, caseData).find((a) => a.action === paymentConfirm.action)?.hint} This
                            can&apos;t be undone.
                          </p>
                          <input
                            type="text"
                            value={paymentNotes}
                            onChange={(e) => setPaymentNotes(e.target.value)}
                            placeholder="Add a note for the other side (optional)"
                            maxLength={2000}
                            className={inputClass}
                          />
                          <div className="flex gap-2">
                            <Button size="sm" variant="primary" loading={paymentBusy} onClick={handlePaymentResolution}>
                              Confirm
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setPaymentConfirm(null)}>
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex flex-wrap gap-2">
                          {paymentActionsFor(viewerRole, caseData).map((a) => (
                            <Button
                              key={a.action}
                              size="sm"
                              variant={a.action === "WITHDRAW" ? "ghost" : "outline"}
                              onClick={() => {
                                setPaymentConfirm({ claimId: claim.id, action: a.action });
                                setPaymentNotes("");
                              }}
                            >
                              {a.label}
                            </Button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                  {TERMINAL_CLAIM_STATUSES.has(claim.status) && claim.authorityClass === "A0" && (
                    <div>
                      {reviewClaimId === claim.id ? (
                        <div className="flex flex-col gap-2 sm:flex-row">
                          <input
                            type="text"
                            value={reviewReason}
                            onChange={(e) => setReviewReason(e.target.value)}
                            placeholder="Why should this be reviewed again?"
                            className={inputClass}
                          />
                          <div className="flex gap-2">
                            <Button size="sm" variant="primary" loading={claimBusy} onClick={() => handleRequestReview(claim.id)}>
                              Send
                            </Button>
                            <Button size="sm" variant="ghost" onClick={() => setReviewClaimId(null)}>
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <Button size="sm" variant="ghost" onClick={() => { setReviewClaimId(claim.id); setReviewReason(""); }}>
                          Request review
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Evidence */}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Evidence</p>
            {evidence.length > 0 && (
              <div className="space-y-2">
                {evidence.map((item) => {
                  // Only the uploader can delete their own item.
                  const mine = viewerRole === "RENTER" ? Boolean(item.uploadedByGuestId) : Boolean(item.uploadedByPartyId);
                  if (item.deletedAt) {
                    return (
                      <div key={item.id} className="rounded-xl bg-slate-50 p-3 text-xs text-slate-400 dark:bg-slate-800/60">
                        Evidence deleted on {formatDate(item.deletedAt)}.
                      </div>
                    );
                  }
                  return (
                  <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                    <div className="min-w-0">
                      <p className="font-medium">{item.hasFile ? item.originalFilename || "File" : "Note"}</p>
                      <p className="text-slate-400">
                        {item.hasFile && `${(item.sizeBytes / 1024).toFixed(1)} KB · `}
                        {formatDate(item.createdAt)} · {label(item.disclosureClass)}
                      </p>
                      {item.noteText && <p className="mt-0.5">&ldquo;{item.noteText}&rdquo;</p>}
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge tone={item.verificationStatus === "VERIFIED" ? "success" : "neutral"}>{label(item.verificationStatus)}</Badge>
                      {item.hasFile ? (
                        <a href={client.evidenceFileUrl(caseId, item.id)} target="_blank" rel="noreferrer">
                          <Button size="sm" variant="outline">
                            <Download className="h-3.5 w-3.5" /> Download
                          </Button>
                        </a>
                      ) : (
                        <span className="text-slate-400">Note only</span>
                      )}
                      {mine && item.legalHold && <span className="text-slate-400">On legal hold — can&apos;t be deleted</span>}
                      {mine && !item.legalHold && confirmDeleteId !== item.id && (
                        <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(item.id)}>
                          Delete
                        </Button>
                      )}
                      {mine && !item.legalHold && confirmDeleteId === item.id && (
                        <>
                          <span className="text-slate-500">Delete permanently?</span>
                          <Button size="sm" variant="outline" loading={evidenceBusy} onClick={() => handleDeleteEvidence(item.id)}>
                            Yes, delete
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setConfirmDeleteId(null)}>
                            Cancel
                          </Button>
                        </>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>
            )}
            <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              <Field label="File">
                <input
                  type="file"
                  onChange={(e) => setEvidenceFile(e.target.files?.[0] ?? null)}
                  className={inputClass}
                />
              </Field>
              <Field label="Note">
                <input type="text" value={evidenceNote} onChange={(e) => setEvidenceNote(e.target.value)} className={inputClass} />
              </Field>
              {caseData.claims.length > 0 && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Relates to claims</p>
                  <div className="space-y-1">
                    {caseData.claims.map((claim) => (
                      <label key={claim.id} className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                        <input
                          type="checkbox"
                          checked={evidenceClaimIds.includes(claim.id)}
                          onChange={() => toggleClaimId(evidenceClaimIds, claim.id, setEvidenceClaimIds)}
                        />
                        {claim.claimCode}
                      </label>
                    ))}
                  </div>
                </div>
              )}
              <Button size="sm" variant="primary" fullWidth loading={evidenceBusy} onClick={handleUploadEvidence}>
                Upload evidence
              </Button>
            </div>
          </div>

          {/* Messages */}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Messages</p>
            <div className="max-h-40 space-y-2 overflow-y-auto rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              {messages.length === 0 ? (
                <p className="text-xs text-slate-400">No messages yet.</p>
              ) : (
                messages.map((m) => (
                  <div key={m.id} className="text-xs text-slate-600 dark:text-slate-300">
                    <span className="font-semibold">{label(m.senderRole)}</span>{" "}
                    <span className="text-slate-400">{formatDate(m.createdAt)}</span>
                    <p>{m.body}</p>
                  </div>
                ))
              )}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                value={messageDraft}
                onChange={(e) => setMessageDraft(e.target.value)}
                placeholder="Write a message..."
                className={inputClass}
              />
              <Button size="sm" variant="primary" loading={messageBusy} onClick={handleSendMessage}>
                Send
              </Button>
            </div>
          </div>

          {/* Settlements */}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Settlements</p>
              <Button size="sm" variant="ghost" onClick={() => setSettlementFormOpen((v) => !v)}>
                <Plus className="h-3.5 w-3.5" /> Propose a settlement
              </Button>
            </div>
            {settlementFormOpen && (
              <div className="space-y-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Claims covered</p>
                  <div className="space-y-1">
                    {caseData.claims.filter((claim) => !isPaymentClaim(claim)).map((claim) => (
                      <label key={claim.id} className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                        <input
                          type="checkbox"
                          checked={settlementClaimIds.includes(claim.id)}
                          onChange={() => toggleClaimId(settlementClaimIds, claim.id, setSettlementClaimIds)}
                        />
                        {claim.claimCode}
                      </label>
                    ))}
                  </div>
                </div>
                <Field label="Terms">
                  <textarea value={settlementTerms} onChange={(e) => setSettlementTerms(e.target.value)} rows={3} className={inputClass} />
                </Field>
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Amount (optional)">
                    <input type="number" min={0} step="0.01" value={settlementAmount} onChange={(e) => setSettlementAmount(e.target.value)} className={inputClass} />
                  </Field>
                  <Field label="Currency">
                    <input type="text" value={settlementCurrency} onChange={(e) => setSettlementCurrency(e.target.value.toUpperCase())} maxLength={3} className={inputClass} />
                  </Field>
                </div>
                <Field label="Expires (optional)">
                  <input type="datetime-local" value={settlementExpiresAt} onChange={(e) => setSettlementExpiresAt(e.target.value)} className={inputClass} />
                </Field>
                <label className="flex items-center gap-2 text-xs text-slate-600 dark:text-slate-300">
                  <input type="checkbox" checked={settlementAck} onChange={(e) => setSettlementAck(e.target.checked)} />
                  I acknowledge this does not waive any non-waivable legal right
                </label>
                <Button
                  size="sm"
                  variant="primary"
                  fullWidth
                  loading={settlementBusy}
                  onClick={handleProposeSettlement}
                >
                  Send proposal
                </Button>
              </div>
            )}
            <div className="space-y-2">
              {settlements.length === 0 ? (
                <p className="text-xs text-slate-400">No settlements yet.</p>
              ) : (
                settlements.map((s) => {
                  const isIncoming = s.status === "SENT" && s.proposedByRole !== viewerRole;
                  const isOwnVoidable = s.proposedByRole === viewerRole && !TERMINAL_SETTLEMENT_STATUSES.has(s.status);
                  const busy = settlementActionBusy === s.id;
                  return (
                    <div key={s.id} className="space-y-1.5 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600 dark:text-slate-300">
                        <span>Proposed by {label(s.proposedByRole)}</span>
                        <Badge tone={disputeSettlementStatusTone[s.status] ?? "neutral"}>{label(s.status)}</Badge>
                      </div>
                      <p className="text-xs text-slate-600 dark:text-slate-300">{s.termsText}</p>
                      <p className="text-xs text-slate-400">
                        {s.amount != null && <>{formatCurrency(s.amount, s.currency)} · </>}
                        {s.expiresAt && <>Expires {formatDate(s.expiresAt)}</>}
                      </p>
                      {isIncoming && (
                        <div className="space-y-2">
                          <div className="flex gap-2">
                            <Button size="sm" variant="primary" loading={busy} onClick={() => handleRespondSettlement(s.id, "ACCEPT")}>
                              Accept
                            </Button>
                            <Button size="sm" variant="accent" loading={busy} onClick={() => handleRespondSettlement(s.id, "REJECT")}>
                              Reject
                            </Button>
                            <Button size="sm" variant="outline" onClick={() => setCounterTargetId(counterTargetId === s.id ? null : s.id)}>
                              Counter
                            </Button>
                          </div>
                          {counterTargetId === s.id && (
                            <div className="space-y-2 rounded-lg bg-white p-2.5 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-white/10">
                              <textarea
                                value={counterTerms}
                                onChange={(e) => setCounterTerms(e.target.value)}
                                rows={2}
                                placeholder="Counter-offer terms"
                                className={inputClass}
                              />
                              <div className="grid grid-cols-2 gap-2">
                                <input
                                  type="number"
                                  min={0}
                                  step="0.01"
                                  placeholder="Amount"
                                  value={counterAmount}
                                  onChange={(e) => setCounterAmount(e.target.value)}
                                  className={inputClass}
                                />
                                <input
                                  type="text"
                                  value={counterCurrency}
                                  onChange={(e) => setCounterCurrency(e.target.value.toUpperCase())}
                                  maxLength={3}
                                  className={inputClass}
                                />
                              </div>
                              <input
                                type="datetime-local"
                                value={counterExpiresAt}
                                onChange={(e) => setCounterExpiresAt(e.target.value)}
                                className={inputClass}
                              />
                              <Button size="sm" variant="primary" fullWidth loading={busy} onClick={handleCounterSettlement}>
                                Send counter-offer
                              </Button>
                            </div>
                          )}
                        </div>
                      )}
                      {isOwnVoidable && (
                        <Button size="sm" variant="ghost" loading={busy} onClick={() => handleVoidSettlement(s.id)}>
                          Void
                        </Button>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Deadlines */}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Deadlines</p>
            {deadlines.length === 0 ? (
              <p className="text-xs text-slate-400">No deadlines set.</p>
            ) : (
              <div className="space-y-1.5">
                {deadlines.map((d) => (
                  <div key={d.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                    <span>{label(d.deadlineType)} — {formatDate(d.dueAt)}</span>
                    <span className="flex items-center gap-2">
                      {d.isOverdue && <Badge tone="danger">Overdue</Badge>}
                      <Badge tone={disputeDeadlineStatusTone[d.status] ?? "neutral"}>{label(d.status)}</Badge>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Parties */}
          <div className="space-y-2 border-t border-slate-200 pt-3 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Parties</p>
            {parties.length === 0 ? (
              <p className="text-xs text-slate-400">No parties recorded.</p>
            ) : (
              <div className="space-y-1.5">
                {parties.map((p) => (
                  <div key={p.id} className="rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                    <span className="font-semibold">{label(p.partyRole)}</span> · {label(p.representationType)}
                    {p.communicationRestrictions && <p className="mt-0.5 text-slate-400">{p.communicationRestrictions}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* External proceedings */}
          <div className="space-y-2 border-t border-slate-200 pt-3 pb-1 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">External proceedings</p>
            {external.length === 0 ? (
              <p className="text-xs text-slate-400">None filed.</p>
            ) : (
              <div className="space-y-1.5">
                {external.map((e) => (
                  <div key={e.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
                    <span>
                      {e.authorityType}
                      {e.finalityState ? ` · ${label(e.finalityState)}` : ""}
                      {e.decisionDate ? ` · Decided ${formatDate(e.decisionDate)}` : ""}
                    </span>
                    <span className="flex items-center gap-2">
                      <Badge tone={disputeExternalProceedingStatusTone[e.status] ?? "neutral"}>{label(e.status)}</Badge>
                      {e.status !== "DECIDED" && e.filedByAdminId == null && decisionFor !== e.id && (
                        <Button size="sm" variant="ghost" onClick={() => { setDecisionFor(e.id); setDecisionDate(""); }}>
                          Record decision
                        </Button>
                      )}
                    </span>
                    {decisionFor === e.id && (
                      <div className="w-full space-y-2 pt-2">
                        <select className={selectClass} value={decisionOutcome} onChange={(ev) => setDecisionOutcome(ev.target.value)}>
                          {OUTSIDE_OUTCOMES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                        <input type="date" className={inputClass} value={decisionDate} onChange={(ev) => setDecisionDate(ev.target.value)} />
                        <label className="flex items-center gap-2">
                          <input type="checkbox" checked={decisionFinal} onChange={(ev) => setDecisionFinal(ev.target.checked)} />
                          This decision is final (no appeal pending)
                        </label>
                        <div className="flex gap-2">
                          <Button size="sm" variant="primary" loading={outsideBusy} onClick={handleRecordDecision}>Save decision</Button>
                          <Button size="sm" variant="ghost" onClick={() => setDecisionFor(null)}>Cancel</Button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : null}
    </Modal>
  );
}

// -- Top-level section --------------------------------------------------

export function DisputeCasesSection({
  client,
  viewerRole,
  cases,
  showToast,
  onChanged,
  occupancyId,
  allowOccupancyInput,
  variant = "embedded",
  emptyMessage,
  openButtonLabel,
}: {
  client: DisputeClient;
  viewerRole: DisputeViewerRole;
  cases: DisputeCaseRead[];
  showToast: (message: string, tone?: ToastTone) => void;
  onChanged: () => void;
  /** Renter mode: this section is scoped to one occupancy, and "Open a Dispute" always targets it. */
  occupancyId?: number;
  /** Host mode: no fixed occupancy, so the open-case form asks for one. */
  allowOccupancyInput?: boolean;
  /** "embedded" matches the compact per-occupancy-card styling (termination/condition report);
   *  "page" wraps each case in its own Card, for a flat top-level list like sublet requests. */
  variant?: "embedded" | "page";
  emptyMessage?: string;
  openButtonLabel?: string;
}) {
  const [openFormVisible, setOpenFormVisible] = useState(false);
  const [detailCaseId, setDetailCaseId] = useState<number | null>(null);
  const [exportCaseId, setExportCaseId] = useState<number | null>(null);
  // Currency already used on these tenancies' claims; blank lets the backend
  // take the tenancy's own currency (crud/disputes.py:_claim_currency).
  const knownCurrency = cases.flatMap((c) => c.claims).find((claim) => claim.currency)?.currency ?? "";
  const detailCurrency =
    cases.find((c) => c.id === detailCaseId)?.claims.find((claim) => claim.currency)?.currency ?? knownCurrency;

  function renderCaseRow(c: DisputeCaseRead) {
    return (
      <div key={c.id} className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-slate-400">#{c.id}</span>
            <Badge tone={disputeSeverityTone[c.severity] ?? "neutral"}>{c.severity}</Badge>
            <Badge tone={disputeCaseStatusTone[c.status] ?? "neutral"}>{label(c.status)}</Badge>
          </div>
          <span className="text-xs text-slate-400">Opened {formatDate(c.openedAt)}</span>
        </div>
        <p className="text-xs font-medium text-primary-700 dark:text-primary-300">
          {disputeClaimFamilyLabel[c.primaryClaimFamily] ?? c.primaryClaimFamily}
        </p>
        <div className="space-y-1">
          {c.claims.map((claim) => (
            <div key={claim.id} className="flex flex-wrap items-center justify-between gap-2 text-xs text-slate-600 dark:text-slate-300">
              <span>
                {claim.claimCode} · {disputeClaimFamilyLabel[claim.claimFamily] ?? claim.claimFamily}
              </span>
              <span className="flex items-center gap-2">
                {claim.amount != null && <span>{formatCurrency(claim.amount, claim.currency)}</span>}
                {claim.outcome && <span className="italic">{label(claim.outcome)}</span>}
                <Badge tone={disputeClaimStatusTone[claim.status] ?? "neutral"}>{label(claim.status)}</Badge>
              </span>
            </div>
          ))}
        </div>
        <div className="flex gap-2 pt-1">
          <Button size="sm" variant="outline" onClick={() => setDetailCaseId(c.id)}>
            View / manage
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setExportCaseId(c.id)}>
            Export
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className={variant === "embedded" ? "mt-4 space-y-2" : "space-y-3"}>
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Disputes</p>
        <Button size="sm" variant="ghost" onClick={() => setOpenFormVisible(true)}>
          <Gavel className="h-3.5 w-3.5" /> {openButtonLabel ?? "Open a Dispute"}
        </Button>
      </div>

      {cases.length === 0 ? (
        variant === "page" ? (
          <Card>
            <div className="flex flex-col items-center gap-3 py-10 text-center">
              <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                <Scale className="h-6 w-6" />
              </span>
              <EmptyState message={emptyMessage ?? "No disputes yet."} />
            </div>
          </Card>
        ) : (
          <p className="flex items-center gap-1.5 text-xs text-slate-400">
            <FileWarning className="h-3.5 w-3.5" /> {emptyMessage ?? "No disputes on this occupancy."}
          </p>
        )
      ) : variant === "page" ? (
        <div className="space-y-3">{cases.map((c) => <Card key={c.id}>{renderCaseRow(c)}</Card>)}</div>
      ) : (
        <div className="space-y-2">{cases.map(renderCaseRow)}</div>
      )}

      <OpenCaseModal
        open={openFormVisible}
        onClose={() => setOpenFormVisible(false)}
        client={client}
        occupancyId={occupancyId}
        allowOccupancyInput={allowOccupancyInput}
        defaultCurrency={knownCurrency}
        showToast={showToast}
        onCreated={onChanged}
      />

      {detailCaseId !== null && (
        <DisputeCaseDetail
          key={detailCaseId}
          open={detailCaseId !== null}
          onClose={() => setDetailCaseId(null)}
          client={client}
          viewerRole={viewerRole}
          caseId={detailCaseId}
          defaultCurrency={detailCurrency}
          showToast={showToast}
          onChanged={onChanged}
        />
      )}

      <ExportModal open={exportCaseId !== null} onClose={() => setExportCaseId(null)} client={client} caseId={exportCaseId} />
    </div>
  );
}
