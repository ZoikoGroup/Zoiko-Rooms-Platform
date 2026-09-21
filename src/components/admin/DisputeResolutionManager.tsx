"use client";

// Admin UI for the ZR-ENG-CLR-010 Dispute Resolution engine
// (backend/app/api/routes/disputes.py's admin_router, prefix /api/admin/disputes).
// This is a separate, much larger surface from the older finance.DisputeCase
// (chargeback) section in FinanceOpsManager.tsx -- do not merge the two.
//
// Note: the admin API has no GET endpoint for listing financial holds on a
// claim, so holds are tracked client-side per case-detail session: they
// appear here once created (or already-known, if you decide one this
// session), and are kept in sync as they move through
// approve/release/confirm-release. Reopening the case detail from the list
// clears this local cache since the server has no way to repopulate it.

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ChevronDown, ChevronUp, RefreshCw } from "lucide-react";
import {
  DisputeCaseExportRead,
  DisputeCaseMessageRead,
  DisputeCaseReopenGrounds,
  DisputeCaseRead,
  DisputeCaseTeam,
  DisputeClaimDecideOutcome,
  DisputeClaimRead,
  DisputeClaimStatus,
  DisputeDeadlineRead,
  DisputeDeadlineType,
  DisputeDecisionRead,
  DisputeEvidenceDisclosureClass,
  DisputeEvidenceRead,
  DisputeExternalProceedingFinalityState,
  DisputeExternalProceedingOutcome,
  DisputeExternalProceedingRead,
  DisputeExternalProceedingStatus,
  DisputeFinancialHoldRead,
  DisputeLegalHoldRead,
  DisputePartyRead,
  DisputePartyRepresentationType,
  DisputeSettlementRead,
} from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import {
  disputeCaseReopenGroundsLabel,
  disputeCaseStatusTone,
  disputeClaimFamilyLabel,
  disputeClaimStatusTone,
  disputeDeadlineStatusTone,
  disputeExternalProceedingStatusTone,
  disputeFinancialHoldStatusTone,
  disputeSettlementStatusTone,
  disputeSeverityTone,
  formatClassificationLabel,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function evidenceFileUrl(evidenceId: number): string {
  return `${API_URL}/api/admin/disputes/evidence/${evidenceId}/file`;
}

function formatDateTime(value: string): string {
  return new Date(value).toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const TEAM_OPTIONS: DisputeCaseTeam[] = ["DISPUTE_OPERATIONS", "TRUST_AND_SAFETY", "LEGAL_COMPLIANCE", "FINANCE"];
const TERMINAL_CLAIM_STATUSES: DisputeClaimStatus[] = ["UPHELD", "PARTLY_UPHELD", "NOT_UPHELD", "SETTLED", "WITHDRAWN"];
const DISCLOSURE_CLASSES: DisputeEvidenceDisclosureClass[] = ["ALL_PARTIES", "HOST_VISIBLE_ONLY", "RENTER_VISIBLE_ONLY", "INTERNAL_ONLY"];
const REP_TYPES: DisputePartyRepresentationType[] = ["PROPERTY_MANAGER", "LEGAL_COUNSEL", "OTHER_AUTHORIZED"];
const PROCEEDING_STATUSES: DisputeExternalProceedingStatus[] = ["ACCEPTED", "PENDING", "DISMISSED", "WITHDRAWN"];
const REOPEN_GROUNDS: DisputeCaseReopenGrounds[] = [
  "MATERIAL_NEW_EVIDENCE",
  "PROCESSING_ERROR",
  "EXTERNAL_DECISION",
  "FRAUD_FINDING",
  "INTERNAL_REVIEW_REQUESTED",
  "OTHER",
];

const smallInputClass =
  "w-full rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700";

interface CaseDetail {
  case: DisputeCaseRead;
  evidence: DisputeEvidenceRead[];
  legalHolds: DisputeLegalHoldRead[];
  settlements: DisputeSettlementRead[];
  deadlines: DisputeDeadlineRead[];
  messages: DisputeCaseMessageRead[];
  parties: DisputePartyRead[];
  proceedings: DisputeExternalProceedingRead[];
  decisions: DisputeDecisionRead[];
}

export function DisputeResolutionManager() {
  const [cases, setCases] = useState<DisputeCaseRead[]>([]);
  const [loadingCases, setLoadingCases] = useState(false);
  const [teamFilter, setTeamFilter] = useState<DisputeCaseTeam | "all">("all");
  const [toast, setToast] = useState("");
  const [sweeping, setSweeping] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const [selectedCaseId, setSelectedCaseId] = useState<number | null>(null);
  const [caseDetail, setCaseDetail] = useState<CaseDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({ claims: true });
  const [holdsByClaim, setHoldsByClaim] = useState<Record<number, DisputeFinancialHoldRead[]>>({});

  // -- Assign --
  const [assignTeam, setAssignTeam] = useState<DisputeCaseTeam | "">("");
  const [assignAdminId, setAssignAdminId] = useState("");

  // -- Claims: decide + financial hold --
  const [decideOpenFor, setDecideOpenFor] = useState<number | null>(null);
  const [decideOutcome, setDecideOutcome] = useState<DisputeClaimDecideOutcome>("UPHELD");
  const [decideReasonCode, setDecideReasonCode] = useState("");
  const [decideReasonCategory, setDecideReasonCategory] = useState("");

  const [holdOpenFor, setHoldOpenFor] = useState<number | null>(null);
  const [holdAmount, setHoldAmount] = useState("");
  const [holdCurrency, setHoldCurrency] = useState("USD");
  const [holdAuthorityBasis, setHoldAuthorityBasis] = useState("");
  const [holdReasonCode, setHoldReasonCode] = useState("");
  const [holdReviewAt, setHoldReviewAt] = useState("");

  // -- Evidence --
  const [evFile, setEvFile] = useState<File | null>(null);
  const [evNote, setEvNote] = useState("");
  const [evDisclosure, setEvDisclosure] = useState<DisputeEvidenceDisclosureClass | "">("");
  const [evClaimIds, setEvClaimIds] = useState<number[]>([]);
  const [evCapturedAt, setEvCapturedAt] = useState("");
  const [redactingFor, setRedactingFor] = useState<number | null>(null);
  const [redactFile, setRedactFile] = useState<File | null>(null);

  // -- Deadlines --
  const [deadlineType, setDeadlineType] = useState<DisputeDeadlineType>("PARTY_RESPONSE");
  const [deadlineDueAt, setDeadlineDueAt] = useState("");
  const [deadlineClaimId, setDeadlineClaimId] = useState("");
  const [extendFor, setExtendFor] = useState<number | null>(null);
  const [extendNewDueAt, setExtendNewDueAt] = useState("");
  const [extendBasis, setExtendBasis] = useState("");

  // -- Messages --
  const [msgBody, setMsgBody] = useState("");
  const [msgInternal, setMsgInternal] = useState(false);

  // -- Parties --
  const [repRepresents, setRepRepresents] = useState<"RENTER" | "HOST">("RENTER");
  const [repType, setRepType] = useState<DisputePartyRepresentationType>("PROPERTY_MANAGER");
  const [repAuthorityRef, setRepAuthorityRef] = useState("");
  const [repGuestId, setRepGuestId] = useState("");
  const [repPartyId, setRepPartyId] = useState("");
  const [commRestrictionDraft, setCommRestrictionDraft] = useState<Record<number, string>>({});

  // -- External proceedings --
  const [epAuthorityType, setEpAuthorityType] = useState("");
  const [epAuthorityName, setEpAuthorityName] = useState("");
  const [epExternalRef, setEpExternalRef] = useState("");
  const [epClaimIds, setEpClaimIds] = useState<number[]>([]);
  const [epFiledAt, setEpFiledAt] = useState("");
  const [epStatusDraft, setEpStatusDraft] = useState<Record<number, DisputeExternalProceedingStatus>>({});
  const [decisionOpenFor, setDecisionOpenFor] = useState<number | null>(null);
  const [pdOutcome, setPdOutcome] = useState<DisputeExternalProceedingOutcome>("UPHELD");
  const [pdDecisionDate, setPdDecisionDate] = useState("");
  const [pdFinality, setPdFinality] = useState<DisputeExternalProceedingFinalityState>("FINAL");
  const [pdReasonCode, setPdReasonCode] = useState("");

  // -- Close / Reopen --
  const [forceCloseReason, setForceCloseReason] = useState("");
  const [reopenGrounds, setReopenGrounds] = useState<DisputeCaseReopenGrounds>("MATERIAL_NEW_EVIDENCE");
  const [reopenNote, setReopenNote] = useState("");
  const [reopenClaimIds, setReopenClaimIds] = useState<number[]>([]);

  // -- Export --
  const [exportOpen, setExportOpen] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const [exportData, setExportData] = useState<DisputeCaseExportRead | null>(null);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  function toggleSection(key: string) {
    setOpenSections((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  function sectionHeader(title: string, key: string, count?: number) {
    const open = Boolean(openSections[key]);
    return (
      <button
        type="button"
        onClick={() => toggleSection(key)}
        className="flex w-full items-center justify-between rounded-xl bg-slate-50 px-4 py-3 text-left text-sm font-semibold text-primary-900 dark:bg-slate-800 dark:text-white"
      >
        <span>
          {title}
          {typeof count === "number" ? ` (${count})` : ""}
        </span>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>
    );
  }

  async function runAction<T>(
    key: string,
    fn: () => Promise<T>,
    successMsg: string | ((result: T) => string),
    failureMsg: string,
    onSuccess?: (result: T) => void,
  ): Promise<T | null> {
    setBusy(key);
    try {
      const result = await fn();
      showToast(typeof successMsg === "function" ? successMsg(result) : successMsg);
      onSuccess?.(result);
      return result;
    } catch {
      showToast(failureMsg);
      return null;
    } finally {
      setBusy(null);
    }
  }

  const loadCases = useCallback(async () => {
    setLoadingCases(true);
    try {
      const path = teamFilter === "all" ? "/api/admin/disputes" : `/api/admin/disputes?team=${teamFilter}`;
      const data = await apiClientFetch<DisputeCaseRead[]>(path);
      setCases(data);
    } catch {
      showToast("Failed to load dispute cases");
    } finally {
      setLoadingCases(false);
    }
  }, [teamFilter]);

  useEffect(() => {
    loadCases();
  }, [loadCases]);

  function syncCaseInList(updated: DisputeCaseRead) {
    setCases((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
  }

  const loadCaseDetail = useCallback(async (caseId: number) => {
    setDetailLoading(true);
    try {
      const [c, evidence, legalHolds, settlements, deadlines, messages, parties, proceedings, decisions] = await Promise.all([
        apiClientFetch<DisputeCaseRead>(`/api/admin/disputes/${caseId}`),
        apiClientFetch<DisputeEvidenceRead[]>(`/api/admin/disputes/${caseId}/evidence`),
        apiClientFetch<DisputeLegalHoldRead[]>(`/api/admin/disputes/${caseId}/legal-holds`),
        apiClientFetch<DisputeSettlementRead[]>(`/api/admin/disputes/${caseId}/settlements`),
        apiClientFetch<DisputeDeadlineRead[]>(`/api/admin/disputes/${caseId}/deadlines`),
        apiClientFetch<DisputeCaseMessageRead[]>(`/api/admin/disputes/${caseId}/messages`),
        apiClientFetch<DisputePartyRead[]>(`/api/admin/disputes/${caseId}/parties`),
        apiClientFetch<DisputeExternalProceedingRead[]>(`/api/admin/disputes/${caseId}/external-proceedings`),
        apiClientFetch<DisputeDecisionRead[]>(`/api/admin/disputes/${caseId}/decisions`),
      ]);
      setCaseDetail({ case: c, evidence, legalHolds, settlements, deadlines, messages, parties, proceedings, decisions });
      setAssignTeam(c.assignedTeam ?? "");
      setAssignAdminId(c.assignedAdminId != null ? String(c.assignedAdminId) : "");
      syncCaseInList(c);
    } catch {
      showToast("Failed to load case detail");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  async function openCase(caseId: number) {
    setSelectedCaseId(caseId);
    setCaseDetail(null);
    setHoldsByClaim({});
    setOpenSections({ claims: true });
    setDecideOpenFor(null);
    setHoldOpenFor(null);
    await loadCaseDetail(caseId);
  }

  function closeCaseDetail() {
    setSelectedCaseId(null);
    setCaseDetail(null);
  }

  async function sweepRetention() {
    setSweeping(true);
    try {
      const result = await apiClientFetch<{ deletedCount: number; evidenceIds: number[] }>(
        "/api/admin/disputes/evidence/sweep-retention",
        { method: "POST" },
      );
      showToast(`Retention sweep: ${result.deletedCount} evidence item(s) deleted.`);
      if (caseDetail) loadCaseDetail(caseDetail.case.id);
    } catch {
      showToast("Retention sweep failed");
    } finally {
      setSweeping(false);
    }
  }

  // ---- Assign ----
  async function submitAssign() {
    if (!caseDetail) return;
    await runAction(
      "assign",
      () =>
        apiClientFetch<DisputeCaseRead>(`/api/admin/disputes/${caseDetail.case.id}/assign`, {
          method: "POST",
          body: JSON.stringify({
            team: assignTeam || undefined,
            assignedAdminId: assignAdminId ? Number(assignAdminId) : undefined,
          }),
        }),
      "Case assignment updated",
      "Failed to update assignment",
      (updated) => {
        setCaseDetail((d) => (d ? { ...d, case: updated } : d));
        syncCaseInList(updated);
      },
    );
  }

  // ---- Claims ----
  async function submitDecide(claimId: number) {
    await runAction(
      `decide-${claimId}`,
      () =>
        apiClientFetch<DisputeClaimRead>(`/api/admin/disputes/claims/${claimId}/decision`, {
          method: "POST",
          body: JSON.stringify({
            outcome: decideOutcome,
            reasonCode: decideReasonCode.trim() || undefined,
            reasonCategory: decideReasonCategory.trim() || undefined,
          }),
        }),
      "Claim decided",
      "Failed to decide claim",
      () => {
        setDecideOpenFor(null);
        setDecideReasonCode("");
        setDecideReasonCategory("");
        if (caseDetail) loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function submitHold(claimId: number) {
    await runAction(
      `hold-create-${claimId}`,
      () =>
        apiClientFetch<DisputeFinancialHoldRead>(`/api/admin/disputes/claims/${claimId}/financial-holds`, {
          method: "POST",
          body: JSON.stringify({
            amount: Number(holdAmount),
            currency: holdCurrency,
            authorityBasis: holdAuthorityBasis,
            reasonCode: holdReasonCode.trim() || undefined,
            reviewAt: holdReviewAt ? new Date(holdReviewAt).toISOString() : undefined,
          }),
        }),
      "Financial hold created",
      "Failed to create financial hold",
      (hold) => {
        setHoldsByClaim((prev) => ({ ...prev, [claimId]: [...(prev[claimId] ?? []), hold] }));
        setHoldOpenFor(null);
        setHoldAmount("");
        setHoldAuthorityBasis("");
        setHoldReasonCode("");
        setHoldReviewAt("");
      },
    );
  }

  function updateHoldInState(claimId: number, updated: DisputeFinancialHoldRead) {
    setHoldsByClaim((prev) => ({
      ...prev,
      [claimId]: (prev[claimId] ?? []).map((h) => (h.id === updated.id ? updated : h)),
    }));
  }

  async function approveHold(claimId: number, holdId: number) {
    await runAction(
      `hold-approve-${holdId}`,
      () => apiClientFetch<DisputeFinancialHoldRead>(`/api/admin/disputes/financial-holds/${holdId}/approve`, { method: "POST" }),
      "Hold approved",
      "Failed to approve hold",
      (h) => updateHoldInState(claimId, h),
    );
  }

  async function releaseHold(claimId: number, holdId: number) {
    const releaseReason = window.prompt("Release reason (optional)") ?? "";
    await runAction(
      `hold-release-${holdId}`,
      () =>
        apiClientFetch<DisputeFinancialHoldRead>(`/api/admin/disputes/financial-holds/${holdId}/release`, {
          method: "POST",
          body: JSON.stringify({ releaseReason: releaseReason || undefined }),
        }),
      "Hold release requested",
      "Failed to release hold",
      (h) => updateHoldInState(claimId, h),
    );
  }

  async function confirmReleaseHold(claimId: number, holdId: number) {
    await runAction(
      `hold-confirm-${holdId}`,
      () => apiClientFetch<DisputeFinancialHoldRead>(`/api/admin/disputes/financial-holds/${holdId}/confirm-release`, { method: "POST" }),
      "Hold release confirmed",
      "Failed to confirm release",
      (h) => updateHoldInState(claimId, h),
    );
  }

  // ---- Evidence ----
  async function submitEvidence() {
    if (!caseDetail) return;
    const form = new FormData();
    if (evFile) form.set("file", evFile);
    form.set("note_text", evNote);
    evClaimIds.forEach((id) => form.append("claim_ids", String(id)));
    if (evDisclosure) form.set("disclosure_class", evDisclosure);
    if (evCapturedAt) form.set("captured_at", new Date(evCapturedAt).toISOString());
    await runAction(
      "evidence-upload",
      () => apiClientFetch<DisputeEvidenceRead>(`/api/admin/disputes/${caseDetail.case.id}/evidence`, { method: "POST", body: form }),
      "Evidence uploaded",
      "Failed to upload evidence",
      () => {
        setEvFile(null);
        setEvNote("");
        setEvDisclosure("");
        setEvClaimIds([]);
        setEvCapturedAt("");
        loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function toggleVerify(evidenceId: number, verified: boolean) {
    await runAction(
      `ev-verify-${evidenceId}`,
      () =>
        apiClientFetch<DisputeEvidenceRead>(`/api/admin/disputes/evidence/${evidenceId}/verify`, {
          method: "POST",
          body: JSON.stringify({ verified }),
        }),
      verified ? "Evidence verified" : "Evidence marked unverified",
      "Failed to update verification",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  async function archiveEvidence(evidenceId: number) {
    await runAction(
      `ev-archive-${evidenceId}`,
      () => apiClientFetch<DisputeEvidenceRead>(`/api/admin/disputes/evidence/${evidenceId}/archive`, { method: "POST" }),
      "Evidence archived",
      "Failed to archive evidence",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  async function requestDeleteEvidence(evidenceId: number) {
    if (!window.confirm("Request deletion of this evidence item? It may be refused if under legal hold or retention rules.")) return;
    await runAction(
      `ev-delete-${evidenceId}`,
      () => apiClientFetch<DisputeEvidenceRead>(`/api/admin/disputes/evidence/${evidenceId}/delete`, { method: "POST" }),
      (r) => (r.deletedAt ? "Evidence deleted" : r.deletionRefusedReason ? `Deletion refused: ${r.deletionRefusedReason}` : "Deletion requested"),
      "Failed to request deletion",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  async function toggleLegalHold(evidenceId: number, hold: boolean) {
    const reason = window.prompt(hold ? "Reason for legal hold" : "Reason for releasing legal hold") ?? "";
    await runAction(
      `ev-legalhold-${evidenceId}`,
      () =>
        apiClientFetch<DisputeEvidenceRead>(`/api/admin/disputes/evidence/${evidenceId}/legal-hold`, {
          method: "POST",
          body: JSON.stringify({ hold, reason: reason || undefined }),
        }),
      hold ? "Legal hold placed" : "Legal hold released",
      "Failed to update legal hold",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  async function submitRedact(evidenceId: number) {
    if (!redactFile) return;
    const form = new FormData();
    form.set("file", redactFile);
    await runAction(
      `ev-redact-${evidenceId}`,
      () => apiClientFetch<DisputeEvidenceRead>(`/api/admin/disputes/evidence/${evidenceId}/redact`, { method: "POST", body: form }),
      "Redacted copy created",
      "Failed to upload redaction",
      () => {
        setRedactingFor(null);
        setRedactFile(null);
        if (caseDetail) loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  // ---- Deadlines ----
  async function submitDeadline() {
    if (!caseDetail || !deadlineDueAt) return;
    await runAction(
      "deadline-create",
      () =>
        apiClientFetch<DisputeDeadlineRead>(`/api/admin/disputes/${caseDetail.case.id}/deadlines`, {
          method: "POST",
          body: JSON.stringify({
            deadlineType,
            dueAt: new Date(deadlineDueAt).toISOString(),
            claimId: deadlineClaimId ? Number(deadlineClaimId) : undefined,
          }),
        }),
      "Deadline created",
      "Failed to create deadline",
      () => {
        setDeadlineDueAt("");
        setDeadlineClaimId("");
        loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function submitExtend(deadlineId: number) {
    if (!extendNewDueAt) return;
    await runAction(
      `deadline-extend-${deadlineId}`,
      () =>
        apiClientFetch<DisputeDeadlineRead>(`/api/admin/disputes/deadlines/${deadlineId}/extend`, {
          method: "POST",
          body: JSON.stringify({ newDueAt: new Date(extendNewDueAt).toISOString(), extensionBasis: extendBasis }),
        }),
      "Deadline extended",
      "Failed to extend deadline",
      () => {
        setExtendFor(null);
        setExtendNewDueAt("");
        setExtendBasis("");
        if (caseDetail) loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function cancelDeadline(deadlineId: number) {
    await runAction(
      `deadline-cancel-${deadlineId}`,
      () => apiClientFetch<DisputeDeadlineRead>(`/api/admin/disputes/deadlines/${deadlineId}/cancel`, { method: "POST" }),
      "Deadline cancelled",
      "Failed to cancel deadline",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  // ---- Messages ----
  async function submitMessage() {
    if (!caseDetail || !msgBody.trim()) return;
    await runAction(
      "message-post",
      () =>
        apiClientFetch<DisputeCaseMessageRead>(`/api/admin/disputes/${caseDetail.case.id}/messages`, {
          method: "POST",
          body: JSON.stringify({ body: msgBody.trim(), visibilityClass: msgInternal ? "INTERNAL_ONLY" : undefined }),
        }),
      "Message posted",
      "Failed to post message",
      () => {
        setMsgBody("");
        setMsgInternal(false);
        loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function moderateMessage(messageId: number, hidden: boolean) {
    await runAction(
      `message-moderate-${messageId}`,
      () =>
        apiClientFetch<DisputeCaseMessageRead>(`/api/admin/disputes/messages/${messageId}/moderate`, {
          method: "POST",
          body: JSON.stringify({ hidden }),
        }),
      hidden ? "Message hidden" : "Message unhidden",
      "Failed to moderate message",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  // ---- Parties ----
  async function submitRepresentative() {
    if (!caseDetail) return;
    await runAction(
      "party-add",
      () =>
        apiClientFetch<DisputePartyRead>(`/api/admin/disputes/${caseDetail.case.id}/parties/representatives`, {
          method: "POST",
          body: JSON.stringify({
            represents: repRepresents,
            representationType: repType,
            authorityEvidenceRef: repAuthorityRef,
            guestId: repGuestId.trim() || undefined,
            partyId: repPartyId ? Number(repPartyId) : undefined,
          }),
        }),
      "Representative added",
      "Failed to add representative",
      () => {
        setRepAuthorityRef("");
        setRepGuestId("");
        setRepPartyId("");
        loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function submitCommRestriction(disputePartyId: number) {
    const restrictions = commRestrictionDraft[disputePartyId] ?? "";
    await runAction(
      `party-comm-${disputePartyId}`,
      () =>
        apiClientFetch<DisputePartyRead>(`/api/admin/disputes/parties/${disputePartyId}/communication-restrictions`, {
          method: "POST",
          body: JSON.stringify({ restrictions }),
        }),
      "Communication restrictions updated",
      "Failed to update restrictions",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  // ---- External proceedings ----
  async function submitProceeding() {
    if (!caseDetail || !epAuthorityType.trim()) return;
    await runAction(
      "proceeding-file",
      () =>
        apiClientFetch<DisputeExternalProceedingRead>(`/api/admin/disputes/${caseDetail.case.id}/external-proceedings`, {
          method: "POST",
          body: JSON.stringify({
            authorityType: epAuthorityType.trim(),
            authorityName: epAuthorityName.trim() || undefined,
            externalReference: epExternalRef.trim() || undefined,
            claimIds: epClaimIds,
            filedAt: epFiledAt ? new Date(epFiledAt).toISOString() : undefined,
          }),
        }),
      "External proceeding filed",
      "Failed to file external proceeding",
      () => {
        setEpAuthorityType("");
        setEpAuthorityName("");
        setEpExternalRef("");
        setEpClaimIds([]);
        setEpFiledAt("");
        loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  async function submitProceedingStatus(proceedingId: number) {
    const status = epStatusDraft[proceedingId];
    if (!status) return;
    await runAction(
      `proceeding-status-${proceedingId}`,
      () =>
        apiClientFetch<DisputeExternalProceedingRead>(`/api/admin/disputes/external-proceedings/${proceedingId}/status`, {
          method: "POST",
          body: JSON.stringify({ status }),
        }),
      "Proceeding status updated",
      "Failed to update status",
      () => caseDetail && loadCaseDetail(caseDetail.case.id),
    );
  }

  async function submitProceedingDecision(proceedingId: number) {
    if (!pdDecisionDate) return;
    await runAction(
      `proceeding-decision-${proceedingId}`,
      () =>
        apiClientFetch<DisputeExternalProceedingRead>(`/api/admin/disputes/external-proceedings/${proceedingId}/decision`, {
          method: "POST",
          body: JSON.stringify({
            outcome: pdOutcome,
            decisionDate: pdDecisionDate,
            finalityState: pdFinality,
            reasonCode: pdReasonCode.trim() || undefined,
          }),
        }),
      "Proceeding decision recorded",
      "Failed to record decision",
      () => {
        setDecisionOpenFor(null);
        setPdDecisionDate("");
        setPdReasonCode("");
        if (caseDetail) loadCaseDetail(caseDetail.case.id);
      },
    );
  }

  // ---- Close / Reopen ----
  async function submitClose() {
    if (!caseDetail) return;
    await runAction(
      "case-close",
      () =>
        apiClientFetch<DisputeCaseRead>(`/api/admin/disputes/${caseDetail.case.id}/close`, {
          method: "POST",
          body: JSON.stringify(forceCloseReason.trim() ? { forceCloseReason: forceCloseReason.trim() } : {}),
        }),
      "Case closed",
      "Failed to close case",
      (updated) => {
        setForceCloseReason("");
        setCaseDetail((d) => (d ? { ...d, case: updated } : d));
        syncCaseInList(updated);
      },
    );
  }

  async function submitReopen() {
    if (!caseDetail) return;
    await runAction(
      "case-reopen",
      () =>
        apiClientFetch<DisputeCaseRead>(`/api/admin/disputes/${caseDetail.case.id}/reopen`, {
          method: "POST",
          body: JSON.stringify({
            grounds: reopenGrounds,
            note: reopenNote.trim() || undefined,
            claimIds: reopenClaimIds.length ? reopenClaimIds : undefined,
          }),
        }),
      "Case reopened",
      "Failed to reopen case",
      (updated) => {
        setReopenNote("");
        setReopenClaimIds([]);
        setCaseDetail((d) => (d ? { ...d, case: updated } : d));
        syncCaseInList(updated);
      },
    );
  }

  // ---- Export ----
  async function openExport() {
    if (!caseDetail) return;
    setExportOpen(true);
    setExportLoading(true);
    try {
      const data = await apiClientFetch<DisputeCaseExportRead>(`/api/admin/disputes/${caseDetail.case.id}/export`);
      setExportData(data);
    } catch {
      showToast("Failed to load case export");
    } finally {
      setExportLoading(false);
    }
  }

  function toggleIdIn(list: number[], id: number): number[] {
    return list.includes(id) ? list.filter((x) => x !== id) : [...list, id];
  }

  const claims = caseDetail?.case.claims ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-white p-4 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">Team</span>
          <select
            value={teamFilter}
            onChange={(e) => setTeamFilter(e.target.value as DisputeCaseTeam | "all")}
            className="rounded-xl bg-slate-50 px-3.5 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          >
            <option value="all">All teams</option>
            {TEAM_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {formatClassificationLabel(t)}
              </option>
            ))}
          </select>
          <Button size="sm" variant="ghost" onClick={() => loadCases()} disabled={loadingCases}>
            <RefreshCw className={`h-3.5 w-3.5 ${loadingCases ? "animate-spin" : ""}`} /> Refresh
          </Button>
        </div>
        <Button size="sm" variant="outline" loading={sweeping} onClick={sweepRetention}>
          Run Retention Sweep
        </Button>
      </div>

      <div className="overflow-x-auto rounded-2xl bg-white shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <table className="w-full min-w-[820px] text-left text-sm">
          <thead>
            <tr className="border-b border-slate-100 text-xs font-semibold uppercase tracking-wide text-slate-400 dark:border-slate-800 dark:text-slate-400">
              <th className="py-3 pl-5 pr-4">Case</th>
              <th className="py-3 pr-4">Severity</th>
              <th className="py-3 pr-4">Status</th>
              <th className="py-3 pr-4">Claim family</th>
              <th className="py-3 pr-4">Team</th>
              <th className="py-3 pr-4">Opened</th>
              <th className="py-3 pr-4">Occupancy / Property</th>
            </tr>
          </thead>
          <tbody>
            {cases.map((c) => (
              <tr
                key={c.id}
                onClick={() => openCase(c.id)}
                className="cursor-pointer border-b border-slate-50 transition-colors hover:bg-primary-50/50 dark:border-slate-800 dark:hover:bg-primary-500/10"
              >
                <td className="py-3.5 pl-5 pr-4 font-mono text-xs font-semibold text-primary-700 dark:text-primary-300">#{c.id}</td>
                <td className="py-3.5 pr-4">
                  <Badge tone={disputeSeverityTone[c.severity] ?? "neutral"}>{c.severity}</Badge>
                </td>
                <td className="py-3.5 pr-4">
                  <Badge tone={disputeCaseStatusTone[c.status] ?? "neutral"}>{formatClassificationLabel(c.status)}</Badge>
                </td>
                <td className="py-3.5 pr-4 text-slate-600 dark:text-slate-300">
                  {disputeClaimFamilyLabel[c.primaryClaimFamily] ?? c.primaryClaimFamily}
                </td>
                <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">
                  {c.assignedTeam ? formatClassificationLabel(c.assignedTeam) : "Unassigned"}
                </td>
                <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">{formatDate(c.openedAt)}</td>
                <td className="py-3.5 pr-4 text-slate-500 dark:text-slate-400">
                  {c.occupancyId ? `Occupancy #${c.occupancyId}` : c.propertyId ? `Property #${c.propertyId}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {cases.length === 0 && !loadingCases && (
          <p className="py-10 text-center text-sm text-slate-400 dark:text-slate-400">No dispute cases for this filter.</p>
        )}
      </div>

      <Modal open={selectedCaseId !== null} onClose={closeCaseDetail} title={caseDetail ? `Dispute Case #${caseDetail.case.id}` : "Dispute Case"} size="xl">
        {detailLoading || !caseDetail ? (
          <p className="py-8 text-center text-sm text-slate-400">Loading case…</p>
        ) : (
          <div className="max-h-[75vh] space-y-4 overflow-y-auto pr-1">
            {/* Overview */}
            <div className="space-y-3 rounded-xl bg-slate-50 p-4 dark:bg-slate-800">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={disputeSeverityTone[caseDetail.case.severity] ?? "neutral"}>{caseDetail.case.severity}</Badge>
                <Badge tone={disputeCaseStatusTone[caseDetail.case.status] ?? "neutral"}>{formatClassificationLabel(caseDetail.case.status)}</Badge>
                <span className="text-xs text-slate-500 dark:text-slate-400">
                  {disputeClaimFamilyLabel[caseDetail.case.primaryClaimFamily] ?? caseDetail.case.primaryClaimFamily}
                </span>
                <span className="text-xs text-slate-400">Opened {formatDate(caseDetail.case.openedAt)}</span>
                <Button size="sm" variant="ghost" onClick={openExport}>
                  Export
                </Button>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <select
                  value={assignTeam}
                  onChange={(e) => setAssignTeam(e.target.value as DisputeCaseTeam | "")}
                  className={smallInputClass}
                >
                  <option value="">Unassigned team</option>
                  {TEAM_OPTIONS.map((t) => (
                    <option key={t} value={t}>
                      {formatClassificationLabel(t)}
                    </option>
                  ))}
                </select>
                <input
                  value={assignAdminId}
                  onChange={(e) => setAssignAdminId(e.target.value)}
                  placeholder="Assigned admin ID"
                  className={smallInputClass}
                />
                <Button size="sm" variant="outline" loading={busy === "assign"} onClick={submitAssign}>
                  Save Assignment
                </Button>
              </div>

              <div className="flex flex-wrap items-center gap-2 border-t border-slate-200 pt-3 dark:border-slate-700">
                {caseDetail.case.status !== "CLOSED" && (
                  <div className="flex flex-1 min-w-[220px] items-center gap-2">
                    <input
                      value={forceCloseReason}
                      onChange={(e) => setForceCloseReason(e.target.value)}
                      placeholder="Force-close reason (leave blank for normal close)"
                      className={smallInputClass}
                    />
                    <Button size="sm" variant="outline" loading={busy === "case-close"} onClick={submitClose}>
                      Close Case
                    </Button>
                  </div>
                )}
                {caseDetail.case.status === "CLOSED" && (
                  <div className="flex flex-1 min-w-[280px] flex-wrap items-center gap-2">
                    <select value={reopenGrounds} onChange={(e) => setReopenGrounds(e.target.value as DisputeCaseReopenGrounds)} className={smallInputClass}>
                      {REOPEN_GROUNDS.map((g) => (
                        <option key={g} value={g}>
                          {disputeCaseReopenGroundsLabel[g] ?? g}
                        </option>
                      ))}
                    </select>
                    <input value={reopenNote} onChange={(e) => setReopenNote(e.target.value)} placeholder="Note (optional)" className={smallInputClass} />
                    <Button size="sm" variant="outline" loading={busy === "case-reopen"} onClick={submitReopen}>
                      Reopen Case
                    </Button>
                  </div>
                )}
              </div>
            </div>

            {/* Claims */}
            <div>
              {sectionHeader("Claims", "claims", claims.length)}
              {openSections.claims && (
                <div className="mt-2 space-y-2">
                  {claims.length === 0 && <p className="px-2 text-xs text-slate-400">No claims on this case.</p>}
                  {claims.map((claim) => {
                    const terminal = TERMINAL_CLAIM_STATUSES.includes(claim.status);
                    const holds = holdsByClaim[claim.id] ?? [];
                    return (
                      <div key={claim.id} className="space-y-2 rounded-xl bg-white p-3 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="text-sm font-semibold text-primary-900 dark:text-white">{claim.claimCode}</p>
                            <p className="text-xs text-slate-500 dark:text-slate-400">
                              {disputeClaimFamilyLabel[claim.claimFamily] ?? claim.claimFamily} · {claim.claimantRole} ·{" "}
                              {claim.amount != null ? formatCurrency(claim.amount, claim.currency) : "No amount"}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge tone={disputeClaimStatusTone[claim.status] ?? "neutral"}>{formatClassificationLabel(claim.status)}</Badge>
                            {claim.outcome && <Badge tone="primary">{formatClassificationLabel(claim.outcome)}</Badge>}
                          </div>
                        </div>
                        {claim.requestedRemedy && <p className="text-xs text-slate-500 dark:text-slate-400">Remedy: {claim.requestedRemedy}</p>}

                        <div className="flex flex-wrap gap-2">
                          {!terminal && (
                            <Button size="sm" variant="outline" onClick={() => setDecideOpenFor(decideOpenFor === claim.id ? null : claim.id)}>
                              Decide
                            </Button>
                          )}
                          <Button size="sm" variant="ghost" onClick={() => setHoldOpenFor(holdOpenFor === claim.id ? null : claim.id)}>
                            Financial Hold
                          </Button>
                        </div>

                        {decideOpenFor === claim.id && (
                          <div className="space-y-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-800">
                            <select value={decideOutcome} onChange={(e) => setDecideOutcome(e.target.value as DisputeClaimDecideOutcome)} className={smallInputClass}>
                              <option value="UPHELD">Upheld</option>
                              <option value="PARTLY_UPHELD">Partly upheld</option>
                              <option value="NOT_UPHELD">Not upheld</option>
                            </select>
                            <input value={decideReasonCode} onChange={(e) => setDecideReasonCode(e.target.value)} placeholder="Reason code" className={smallInputClass} />
                            <input
                              value={decideReasonCategory}
                              onChange={(e) => setDecideReasonCategory(e.target.value)}
                              placeholder="Reason category"
                              className={smallInputClass}
                            />
                            <Button size="sm" variant="primary" loading={busy === `decide-${claim.id}`} onClick={() => submitDecide(claim.id)}>
                              Submit Decision
                            </Button>
                          </div>
                        )}

                        {holdOpenFor === claim.id && (
                          <div className="space-y-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-800">
                            <input value={holdAmount} onChange={(e) => setHoldAmount(e.target.value)} placeholder="Amount" className={smallInputClass} />
                            <input value={holdCurrency} onChange={(e) => setHoldCurrency(e.target.value)} placeholder="Currency" className={smallInputClass} />
                            <input
                              value={holdAuthorityBasis}
                              onChange={(e) => setHoldAuthorityBasis(e.target.value)}
                              placeholder="Authority basis"
                              className={smallInputClass}
                            />
                            <input value={holdReasonCode} onChange={(e) => setHoldReasonCode(e.target.value)} placeholder="Reason code (optional)" className={smallInputClass} />
                            <input
                              type="datetime-local"
                              value={holdReviewAt}
                              onChange={(e) => setHoldReviewAt(e.target.value)}
                              className={smallInputClass}
                            />
                            <Button size="sm" variant="primary" loading={busy === `hold-create-${claim.id}`} onClick={() => submitHold(claim.id)}>
                              Create Hold
                            </Button>
                          </div>
                        )}

                        {holds.length > 0 && (
                          <div className="space-y-1.5 border-t border-slate-100 pt-2 dark:border-slate-800">
                            {holds.map((h) => (
                              <div key={h.id} className="flex flex-wrap items-center justify-between gap-2 text-xs">
                                <span className="text-slate-600 dark:text-slate-300">
                                  {formatCurrency(h.amount, h.currency)} · {h.authorityBasis}
                                </span>
                                <div className="flex items-center gap-1.5">
                                  <Badge tone={disputeFinancialHoldStatusTone[h.status] ?? "neutral"}>{formatClassificationLabel(h.status)}</Badge>
                                  {h.status === "PROPOSED" && (
                                    <Button size="sm" variant="ghost" loading={busy === `hold-approve-${h.id}`} onClick={() => approveHold(claim.id, h.id)}>
                                      Approve
                                    </Button>
                                  )}
                                  {h.status === "ACTIVE" && (
                                    <Button size="sm" variant="ghost" loading={busy === `hold-release-${h.id}`} onClick={() => releaseHold(claim.id, h.id)}>
                                      Release
                                    </Button>
                                  )}
                                  {h.status === "RELEASE_PENDING" && (
                                    <Button size="sm" variant="ghost" loading={busy === `hold-confirm-${h.id}`} onClick={() => confirmReleaseHold(claim.id, h.id)}>
                                      Confirm Release
                                    </Button>
                                  )}
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Evidence */}
            <div>
              {sectionHeader("Evidence", "evidence", caseDetail.evidence.length)}
              {openSections.evidence && (
                <div className="mt-2 space-y-3">
                  <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                    <input type="file" onChange={(e) => setEvFile(e.target.files?.[0] ?? null)} className="text-xs" />
                    <textarea value={evNote} onChange={(e) => setEvNote(e.target.value)} placeholder="Note" rows={2} className={smallInputClass} />
                    <select value={evDisclosure} onChange={(e) => setEvDisclosure(e.target.value as DisputeEvidenceDisclosureClass | "")} className={smallInputClass}>
                      <option value="">Default disclosure</option>
                      {DISCLOSURE_CLASSES.map((d) => (
                        <option key={d} value={d}>
                          {formatClassificationLabel(d)}
                        </option>
                      ))}
                    </select>
                    <input type="datetime-local" value={evCapturedAt} onChange={(e) => setEvCapturedAt(e.target.value)} className={smallInputClass} />
                    <div className="flex flex-wrap gap-2 text-xs">
                      {claims.map((c) => (
                        <label key={c.id} className="flex items-center gap-1.5 rounded-full bg-white px-2.5 py-1 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
                          <input type="checkbox" checked={evClaimIds.includes(c.id)} onChange={() => setEvClaimIds((prev) => toggleIdIn(prev, c.id))} />
                          {c.claimCode}
                        </label>
                      ))}
                    </div>
                    <Button size="sm" variant="primary" loading={busy === "evidence-upload"} onClick={submitEvidence}>
                      Upload Evidence
                    </Button>
                  </div>

                  {caseDetail.evidence.map((ev) => (
                    <div key={ev.id} className="space-y-2 rounded-xl bg-white p-3 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <a href={evidenceFileUrl(ev.id)} target="_blank" rel="noreferrer" className="text-sm font-semibold text-primary-700 hover:underline dark:text-primary-300">
                          {ev.originalFilename || `Evidence #${ev.id}`}
                        </a>
                        <span className="text-xs text-slate-400">{(ev.sizeBytes / 1024).toFixed(1)} KB</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        <Badge tone={ev.verificationStatus === "VERIFIED" ? "success" : "neutral"}>{formatClassificationLabel(ev.verificationStatus)}</Badge>
                        <Badge tone="neutral">{formatClassificationLabel(ev.disclosureClass)}</Badge>
                        {ev.legalHold && <Badge tone="danger">Legal Hold</Badge>}
                        {ev.deletedAt && <Badge tone="danger">Deleted</Badge>}
                        {ev.deletionRefusedReason && <Badge tone="warning">Deletion refused</Badge>}
                      </div>
                      {ev.noteText && <p className="text-xs text-slate-500 dark:text-slate-400">{ev.noteText}</p>}
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="ghost" loading={busy === `ev-verify-${ev.id}`} onClick={() => toggleVerify(ev.id, ev.verificationStatus !== "VERIFIED")}>
                          {ev.verificationStatus === "VERIFIED" ? "Unverify" : "Verify"}
                        </Button>
                        <Button size="sm" variant="ghost" loading={busy === `ev-archive-${ev.id}`} onClick={() => archiveEvidence(ev.id)}>
                          Archive
                        </Button>
                        <Button size="sm" variant="ghost" loading={busy === `ev-delete-${ev.id}`} onClick={() => requestDeleteEvidence(ev.id)}>
                          Request Deletion
                        </Button>
                        <Button size="sm" variant="ghost" loading={busy === `ev-legalhold-${ev.id}`} onClick={() => toggleLegalHold(ev.id, !ev.legalHold)}>
                          {ev.legalHold ? "Clear Legal Hold" : "Set Legal Hold"}
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setRedactingFor(redactingFor === ev.id ? null : ev.id)}>
                          Redact
                        </Button>
                      </div>
                      {redactingFor === ev.id && (
                        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 p-2 dark:bg-slate-800">
                          <input type="file" onChange={(e) => setRedactFile(e.target.files?.[0] ?? null)} className="text-xs" />
                          <Button size="sm" variant="primary" loading={busy === `ev-redact-${ev.id}`} onClick={() => submitRedact(ev.id)}>
                            Upload Redacted Copy
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Settlements (read-only) */}
            <div>
              {sectionHeader("Settlements", "settlements", caseDetail.settlements.length)}
              {openSections.settlements && (
                <div className="mt-2 space-y-2">
                  {caseDetail.settlements.length === 0 && <p className="px-2 text-xs text-slate-400">No settlements proposed.</p>}
                  {caseDetail.settlements.map((s) => (
                    <div key={s.id} className="rounded-xl bg-white p-3 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs text-slate-500 dark:text-slate-400">Proposed by {s.proposedByRole}</span>
                        <Badge tone={disputeSettlementStatusTone[s.status] ?? "neutral"}>{formatClassificationLabel(s.status)}</Badge>
                      </div>
                      <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{s.termsText}</p>
                      {s.amount != null && <p className="text-xs text-slate-500 dark:text-slate-400">{formatCurrency(s.amount, s.currency)}</p>}
                      {s.expiresAt && <p className="text-xs text-slate-400">Expires {formatDate(s.expiresAt)}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Deadlines */}
            <div>
              {sectionHeader("Deadlines", "deadlines", caseDetail.deadlines.length)}
              {openSections.deadlines && (
                <div className="mt-2 space-y-3">
                  <div className="flex flex-wrap items-end gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                    <select value={deadlineType} onChange={(e) => setDeadlineType(e.target.value as DisputeDeadlineType)} className={smallInputClass}>
                      <option value="PARTY_RESPONSE">Party response</option>
                      <option value="EVIDENCE_CLOSE">Evidence close</option>
                    </select>
                    <input type="datetime-local" value={deadlineDueAt} onChange={(e) => setDeadlineDueAt(e.target.value)} className={smallInputClass} />
                    <select value={deadlineClaimId} onChange={(e) => setDeadlineClaimId(e.target.value)} className={smallInputClass}>
                      <option value="">No specific claim</option>
                      {claims.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.claimCode}
                        </option>
                      ))}
                    </select>
                    <Button size="sm" variant="primary" loading={busy === "deadline-create"} onClick={submitDeadline}>
                      Create Deadline
                    </Button>
                  </div>

                  {caseDetail.deadlines.map((d) => (
                    <div
                      key={d.id}
                      className={`space-y-2 rounded-xl p-3 ring-1 ${d.isOverdue ? "bg-rose-50 ring-rose-200 dark:bg-rose-500/10 dark:ring-rose-500/20" : "bg-white ring-slate-100 dark:bg-slate-900 dark:ring-white/10"}`}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-medium text-primary-900 dark:text-white">{formatClassificationLabel(d.deadlineType)}</span>
                        <div className="flex items-center gap-1.5">
                          {d.isOverdue && <Badge tone="danger">Overdue</Badge>}
                          <Badge tone={disputeDeadlineStatusTone[d.status] ?? "neutral"}>{formatClassificationLabel(d.status)}</Badge>
                        </div>
                      </div>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Due {formatDateTime(d.dueAt)}</p>
                      {d.status === "PENDING" && (
                        <div className="flex flex-wrap gap-2">
                          <Button size="sm" variant="ghost" onClick={() => setExtendFor(extendFor === d.id ? null : d.id)}>
                            Extend
                          </Button>
                          <Button size="sm" variant="ghost" loading={busy === `deadline-cancel-${d.id}`} onClick={() => cancelDeadline(d.id)}>
                            Cancel
                          </Button>
                        </div>
                      )}
                      {extendFor === d.id && (
                        <div className="flex flex-wrap items-center gap-2 rounded-lg bg-slate-50 p-2 dark:bg-slate-800">
                          <input type="datetime-local" value={extendNewDueAt} onChange={(e) => setExtendNewDueAt(e.target.value)} className={smallInputClass} />
                          <input value={extendBasis} onChange={(e) => setExtendBasis(e.target.value)} placeholder="Extension basis" className={smallInputClass} />
                          <Button size="sm" variant="primary" loading={busy === `deadline-extend-${d.id}`} onClick={() => submitExtend(d.id)}>
                            Confirm Extension
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Messages */}
            <div>
              {sectionHeader("Messages", "messages", caseDetail.messages.length)}
              {openSections.messages && (
                <div className="mt-2 space-y-3">
                  <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                    <textarea value={msgBody} onChange={(e) => setMsgBody(e.target.value)} placeholder="Message body" rows={2} className={smallInputClass} />
                    <label className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                      <input type="checkbox" checked={msgInternal} onChange={(e) => setMsgInternal(e.target.checked)} /> Internal note only
                    </label>
                    <Button size="sm" variant="primary" loading={busy === "message-post"} onClick={submitMessage}>
                      Post Message
                    </Button>
                  </div>
                  {caseDetail.messages.map((m) => (
                    <div key={m.id} className="rounded-xl bg-white p-3 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-xs font-semibold text-slate-500 dark:text-slate-400">
                          {m.senderRole} · {formatDateTime(m.createdAt)}
                        </span>
                        <div className="flex items-center gap-1.5">
                          {m.visibilityClass === "INTERNAL_ONLY" && <Badge tone="neutral">Internal</Badge>}
                          {m.moderationState === "HIDDEN" && <Badge tone="danger">Hidden</Badge>}
                          <Button
                            size="sm"
                            variant="ghost"
                            loading={busy === `message-moderate-${m.id}`}
                            onClick={() => moderateMessage(m.id, m.moderationState !== "HIDDEN")}
                          >
                            {m.moderationState === "HIDDEN" ? "Unhide" : "Hide"}
                          </Button>
                        </div>
                      </div>
                      <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{m.body}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Parties */}
            <div>
              {sectionHeader("Parties", "parties", caseDetail.parties.length)}
              {openSections.parties && (
                <div className="mt-2 space-y-3">
                  <div className="flex flex-wrap items-end gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                    <select value={repRepresents} onChange={(e) => setRepRepresents(e.target.value as "RENTER" | "HOST")} className={smallInputClass}>
                      <option value="RENTER">Renter</option>
                      <option value="HOST">Host</option>
                    </select>
                    <select value={repType} onChange={(e) => setRepType(e.target.value as DisputePartyRepresentationType)} className={smallInputClass}>
                      {REP_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {formatClassificationLabel(t)}
                        </option>
                      ))}
                    </select>
                    <input value={repAuthorityRef} onChange={(e) => setRepAuthorityRef(e.target.value)} placeholder="Authority evidence ref" className={smallInputClass} />
                    <input value={repGuestId} onChange={(e) => setRepGuestId(e.target.value)} placeholder="Guest ID (optional)" className={smallInputClass} />
                    <input value={repPartyId} onChange={(e) => setRepPartyId(e.target.value)} placeholder="Party ID (optional)" className={smallInputClass} />
                    <Button size="sm" variant="primary" loading={busy === "party-add"} onClick={submitRepresentative}>
                      Add Representative
                    </Button>
                  </div>
                  {caseDetail.parties.map((p) => (
                    <div key={p.id} className="space-y-2 rounded-xl bg-white p-3 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge tone="neutral">{p.partyRole}</Badge>
                        <span className="text-xs text-slate-500 dark:text-slate-400">{formatClassificationLabel(p.representationType)}</span>
                        {p.represents && <span className="text-xs text-slate-400">Represents {p.represents}</span>}
                      </div>
                      {p.communicationRestrictions && <p className="text-xs text-amber-600 dark:text-amber-400">Restrictions: {p.communicationRestrictions}</p>}
                      <div className="flex flex-wrap items-center gap-2">
                        <input
                          value={commRestrictionDraft[p.id] ?? p.communicationRestrictions}
                          onChange={(e) => setCommRestrictionDraft((prev) => ({ ...prev, [p.id]: e.target.value }))}
                          placeholder="Communication restrictions (blank clears)"
                          className={smallInputClass}
                        />
                        <Button size="sm" variant="ghost" loading={busy === `party-comm-${p.id}`} onClick={() => submitCommRestriction(p.id)}>
                          Save
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* External proceedings */}
            <div>
              {sectionHeader("External Proceedings", "external", caseDetail.proceedings.length)}
              {openSections.external && (
                <div className="mt-2 space-y-3">
                  <div className="space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
                    <input value={epAuthorityType} onChange={(e) => setEpAuthorityType(e.target.value)} placeholder="Authority type" className={smallInputClass} />
                    <input value={epAuthorityName} onChange={(e) => setEpAuthorityName(e.target.value)} placeholder="Authority name (optional)" className={smallInputClass} />
                    <input value={epExternalRef} onChange={(e) => setEpExternalRef(e.target.value)} placeholder="External reference (optional)" className={smallInputClass} />
                    <input type="date" value={epFiledAt} onChange={(e) => setEpFiledAt(e.target.value)} className={smallInputClass} />
                    <div className="flex flex-wrap gap-2 text-xs">
                      {claims.map((c) => (
                        <label key={c.id} className="flex items-center gap-1.5 rounded-full bg-white px-2.5 py-1 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
                          <input type="checkbox" checked={epClaimIds.includes(c.id)} onChange={() => setEpClaimIds((prev) => toggleIdIn(prev, c.id))} />
                          {c.claimCode}
                        </label>
                      ))}
                    </div>
                    <Button size="sm" variant="primary" loading={busy === "proceeding-file"} onClick={submitProceeding}>
                      File Proceeding
                    </Button>
                  </div>

                  {caseDetail.proceedings.map((p) => (
                    <div key={p.id} className="space-y-2 rounded-xl bg-white p-3 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm font-medium text-primary-900 dark:text-white">{p.authorityType}</span>
                        <div className="flex items-center gap-1.5">
                          <Badge tone={disputeExternalProceedingStatusTone[p.status] ?? "neutral"}>{formatClassificationLabel(p.status)}</Badge>
                          {p.finalityState && <Badge tone="neutral">{formatClassificationLabel(p.finalityState)}</Badge>}
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <select
                          value={epStatusDraft[p.id] ?? ""}
                          onChange={(e) => setEpStatusDraft((prev) => ({ ...prev, [p.id]: e.target.value as DisputeExternalProceedingStatus }))}
                          className={smallInputClass}
                        >
                          <option value="">Update status…</option>
                          {PROCEEDING_STATUSES.map((s) => (
                            <option key={s} value={s}>
                              {formatClassificationLabel(s)}
                            </option>
                          ))}
                        </select>
                        <Button size="sm" variant="ghost" loading={busy === `proceeding-status-${p.id}`} onClick={() => submitProceedingStatus(p.id)}>
                          Update
                        </Button>
                        <Button size="sm" variant="ghost" onClick={() => setDecisionOpenFor(decisionOpenFor === p.id ? null : p.id)}>
                          Record Decision
                        </Button>
                      </div>
                      {decisionOpenFor === p.id && (
                        <div className="space-y-2 rounded-lg bg-slate-50 p-3 dark:bg-slate-800">
                          <select value={pdOutcome} onChange={(e) => setPdOutcome(e.target.value as DisputeExternalProceedingOutcome)} className={smallInputClass}>
                            <option value="UPHELD">Upheld</option>
                            <option value="PARTLY_UPHELD">Partly upheld</option>
                            <option value="NOT_UPHELD">Not upheld</option>
                            <option value="SETTLED">Settled</option>
                          </select>
                          <input type="date" value={pdDecisionDate} onChange={(e) => setPdDecisionDate(e.target.value)} className={smallInputClass} />
                          <select value={pdFinality} onChange={(e) => setPdFinality(e.target.value as DisputeExternalProceedingFinalityState)} className={smallInputClass}>
                            <option value="FINAL">Final</option>
                            <option value="UNDER_REVIEW">Under review</option>
                          </select>
                          <input value={pdReasonCode} onChange={(e) => setPdReasonCode(e.target.value)} placeholder="Reason code (optional)" className={smallInputClass} />
                          <Button size="sm" variant="primary" loading={busy === `proceeding-decision-${p.id}`} onClick={() => submitProceedingDecision(p.id)}>
                            Submit Decision
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Decisions (read-only audit trail) */}
            <div>
              {sectionHeader("Decisions", "decisions", caseDetail.decisions.length)}
              {openSections.decisions && (
                <div className="mt-2 space-y-2">
                  {caseDetail.decisions.length === 0 && <p className="px-2 text-xs text-slate-400">No decisions recorded yet.</p>}
                  {caseDetail.decisions.map((d) => (
                    <div key={d.id} className="rounded-xl bg-white p-3 text-xs ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
                      <span className="font-semibold text-primary-900 dark:text-white">{formatClassificationLabel(d.outcome)}</span>{" "}
                      <span className="text-slate-500 dark:text-slate-400">
                        · claim #{d.claimId} · via {d.authority} · {formatDateTime(d.decidedAt)}
                      </span>
                      {d.decisionBasis && <p className="mt-1 text-slate-500 dark:text-slate-400">{d.decisionBasis}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </Modal>

      <Modal open={exportOpen} onClose={() => setExportOpen(false)} title="Case Export" size="lg">
        {exportLoading || !exportData ? (
          <p className="py-6 text-center text-sm text-slate-400">Loading export…</p>
        ) : (
          <div className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
            <p className="text-xs text-slate-400">Generated {formatDateTime(exportData.generatedAt)}</p>
            <div>
              <h4 className="mb-1.5 text-sm font-semibold text-primary-900 dark:text-white">Chronology</h4>
              <div className="space-y-1.5">
                {exportData.chronology.map((ev, i) => (
                  <div key={i} className="rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-800">
                    <span className="font-semibold text-slate-600 dark:text-slate-300">{formatDateTime(ev.timestamp)}</span> — {ev.eventType}: {ev.summary}
                  </div>
                ))}
                {exportData.chronology.length === 0 && <p className="text-xs text-slate-400">No chronology events.</p>}
              </div>
            </div>
            <div>
              <h4 className="mb-1.5 text-sm font-semibold text-primary-900 dark:text-white">Evidence Index</h4>
              <div className="space-y-1.5">
                {exportData.evidenceIndex.map((ev) => (
                  <div key={ev.id} className="rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-800">
                    {ev.originalFilename || `Evidence #${ev.id}`} · {formatClassificationLabel(ev.verificationStatus)}
                  </div>
                ))}
                {exportData.evidenceIndex.length === 0 && <p className="text-xs text-slate-400">No evidence on file.</p>}
              </div>
            </div>
          </div>
        )}
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
