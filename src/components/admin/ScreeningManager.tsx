"use client";

import { useState } from "react";
import { CheckCircle2, ClipboardCheck } from "lucide-react";
import { ScreeningCheck } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { screeningDecisionStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

// AC-09/AC-10: only these two are genuinely terminal -- INCONCLUSIVE and
// DISPUTED_SOURCE (AC-48) both route to re-review.
const SCREENING_TERMINAL_STATUSES = ["PASS", "FAIL"];

/** ZR-ENG-CLR-012 Sections 10/11: Application/Affordability Evidence and
 * Screening/Consumer-Report Controls -- "a separate optional capability,
 * not smuggled into identity verification." Looked up by party ID (same
 * pattern as OccupancyEligibilityManager). Check type is free text: which
 * categories are even lawful anywhere is jurisdiction policy, never a
 * platform-hardcoded list. */
export function ScreeningManager() {
  const [partyIdInput, setPartyIdInput] = useState("");
  const [partyId, setPartyId] = useState<number | null>(null);
  const [checks, setChecks] = useState<ScreeningCheck[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState("");

  const [openModal, setOpenModal] = useState(false);
  const [jurisdictionCode, setJurisdictionCode] = useState("England");
  const [checkType, setCheckType] = useState("");
  const [providerName, setProviderName] = useState("");
  const [permissiblePurpose, setPermissiblePurpose] = useState("");
  const [hostPolicyCriteria, setHostPolicyCriteria] = useState("");
  const [opening, setOpening] = useState(false);

  const [decideTarget, setDecideTarget] = useState<ScreeningCheck | null>(null);
  const [decisionStatus, setDecisionStatus] = useState<"PASS" | "FAIL" | "INCONCLUSIVE">("PASS");
  const [decisionReason, setDecisionReason] = useState("");
  const [providerResultSummary, setProviderResultSummary] = useState("");
  const [deciding, setDeciding] = useState(false);

  const [disputeTarget, setDisputeTarget] = useState<ScreeningCheck | null>(null);
  const [disputeReason, setDisputeReason] = useState("");
  const [disputing, setDisputing] = useState(false);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  async function loadForParty(id: number) {
    setLoading(true);
    try {
      const data = await apiClientFetch<ScreeningCheck[]>(`/api/verification/screening-checks/party/${id}`);
      setChecks(data);
      setPartyId(id);
    } catch {
      showToast("Failed to load screening checks for that party");
    } finally {
      setLoading(false);
    }
  }

  function handleLookup() {
    const id = Number(partyIdInput);
    if (!Number.isInteger(id) || id <= 0) return showToast("Enter a valid party ID");
    loadForParty(id);
  }

  function openOpenModal() {
    setJurisdictionCode("England");
    setCheckType("");
    setProviderName("");
    setPermissiblePurpose("");
    setHostPolicyCriteria("");
    setOpenModal(true);
  }

  async function submitOpen() {
    if (!partyId) return;
    if (!checkType.trim()) return showToast("Enter a check type");
    if (!permissiblePurpose.trim()) return showToast("A permissible purpose is required");
    setOpening(true);
    try {
      await apiClientFetch("/api/verification/screening-checks", {
        method: "POST",
        body: JSON.stringify({
          partyId, jurisdictionCode: jurisdictionCode.trim(), checkType: checkType.trim().toUpperCase(),
          providerName: providerName.trim(), permissiblePurpose: permissiblePurpose.trim(),
          hostPolicyCriteria: hostPolicyCriteria.trim(),
        }),
      });
      showToast("Screening check opened.");
      setOpenModal(false);
      await loadForParty(partyId);
    } catch {
      showToast("Failed to open screening check");
    } finally {
      setOpening(false);
    }
  }

  function openDecideModal(check: ScreeningCheck) {
    setDecideTarget(check);
    setDecisionStatus("PASS");
    setDecisionReason("");
    setProviderResultSummary("");
  }

  async function submitDecide() {
    if (!decideTarget || !partyId) return;
    setDeciding(true);
    try {
      await apiClientFetch(`/api/verification/screening-checks/${decideTarget.id}/decide`, {
        method: "POST",
        body: JSON.stringify({
          decisionStatus, decisionReason: decisionReason.trim(), providerResultSummary: providerResultSummary.trim(),
        }),
      });
      showToast("Decision recorded.");
      setDecideTarget(null);
      await loadForParty(partyId);
    } catch {
      showToast("Failed to record decision");
    } finally {
      setDeciding(false);
    }
  }

  function openDisputeModal(check: ScreeningCheck) {
    setDisputeTarget(check);
    setDisputeReason("");
  }

  async function submitDispute() {
    if (!disputeTarget || !partyId) return;
    if (!disputeReason.trim()) return showToast("A dispute reason is required");
    setDisputing(true);
    try {
      await apiClientFetch(`/api/verification/screening-checks/${disputeTarget.id}/dispute`, {
        method: "POST",
        body: JSON.stringify({ disputeReason: disputeReason.trim() }),
      });
      showToast("Dispute recorded — check is now re-decidable.");
      setDisputeTarget(null);
      await loadForParty(partyId);
    } catch {
      showToast("Failed to record dispute");
    } finally {
      setDisputing(false);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <ClipboardCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Screening &amp; Affordability</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        A separate, optional capability — never smuggled into identity verification. A permissible purpose is required
        to open a check; the provider&apos;s result is recorded separately from the final decision, which stays a
        human call against the Host&apos;s own policy criteria.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <div>
          <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Party ID</label>
          <input
            type="number"
            value={partyIdInput}
            onChange={(e) => setPartyIdInput(e.target.value)}
            className="w-32 rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
        </div>
        <Button size="sm" variant="outline" loading={loading} onClick={handleLookup}>
          Look up
        </Button>
        {partyId !== null && (
          <Button size="sm" variant="primary" onClick={openOpenModal}>
            Open a check
          </Button>
        )}
      </div>

      {partyId !== null && (
        <div className="mt-4 space-y-2">
          {checks.length === 0 ? (
            <p className="text-sm text-slate-400">No screening checks for this party yet.</p>
          ) : (
            checks.map((check) => (
              <div
                key={check.id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-primary-900 dark:text-white">
                    {check.checkType} · {check.jurisdictionCode}
                  </p>
                  <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{check.permissiblePurpose}</p>
                  <p className="mt-0.5 text-xs text-slate-400">Opened {formatDate(check.createdAt)}</p>
                  {check.decisionStatus === "DISPUTED_SOURCE" && check.disputeReason && (
                    <p className="mt-0.5 text-xs text-primary-600 dark:text-primary-300">Dispute: {check.disputeReason}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={screeningDecisionStatusTone[check.decisionStatus] ?? "neutral"}>{check.decisionStatus.replace(/_/g, " ")}</Badge>
                  {!SCREENING_TERMINAL_STATUSES.includes(check.decisionStatus) && (
                    <Button size="sm" variant="primary" onClick={() => openDecideModal(check)}>
                      Decide
                    </Button>
                  )}
                  {SCREENING_TERMINAL_STATUSES.includes(check.decisionStatus) && (
                    <Button size="sm" variant="outline" onClick={() => openDisputeModal(check)}>
                      Dispute
                    </Button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      )}

      <Modal open={openModal} onClose={() => setOpenModal(false)} title="Open a screening check">
        <div className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Jurisdiction</label>
            <input
              value={jurisdictionCode}
              onChange={(e) => setJurisdictionCode(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Check type</label>
            <input
              value={checkType}
              onChange={(e) => setCheckType(e.target.value)}
              placeholder="e.g. AFFORDABILITY, CREDIT_REPORT"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Provider / source</label>
            <input
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Permissible purpose (required)</label>
            <textarea
              value={permissiblePurpose}
              onChange={(e) => setPermissiblePurpose(e.target.value)}
              rows={2}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Host policy criteria (optional)</label>
            <textarea
              value={hostPolicyCriteria}
              onChange={(e) => setHostPolicyCriteria(e.target.value)}
              rows={2}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpenModal(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={opening} onClick={submitOpen}>
              Open check
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(decideTarget)} onClose={() => setDecideTarget(null)} title="Decide screening check">
        <div className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Decision</label>
            <select
              value={decisionStatus}
              onChange={(e) => setDecisionStatus(e.target.value as typeof decisionStatus)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="PASS">Pass</option>
              <option value="FAIL">Fail</option>
              <option value="INCONCLUSIVE">Inconclusive</option>
            </select>
            {decisionStatus === "FAIL" && (
              <p className="mt-1 text-xs text-amber-600">
                A FAIL decision automatically notifies the applicant of their dispute rights.
              </p>
            )}
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Provider result summary</label>
            <input
              value={providerResultSummary}
              onChange={(e) => setProviderResultSummary(e.target.value)}
              placeholder="e.g. Score: 82/100 — this is a signal, not the decision"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Decision reason</label>
            <textarea
              value={decisionReason}
              onChange={(e) => setDecisionReason(e.target.value)}
              rows={2}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDecideTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={deciding} onClick={submitDecide}>
              Record decision
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(disputeTarget)} onClose={() => setDisputeTarget(null)} title="Record a dispute">
        <div className="space-y-3.5">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            AC-48: the applicant challenges this decision as inaccurate. Recording a dispute makes the check
            re-decidable so it can be re-evaluated with corrected information.
          </p>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Dispute reason</label>
            <textarea
              value={disputeReason}
              onChange={(e) => setDisputeReason(e.target.value)}
              rows={3}
              placeholder="e.g. This report belongs to a different person with a similar name"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDisputeTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={disputing} onClick={submitDispute}>
              Record dispute
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
