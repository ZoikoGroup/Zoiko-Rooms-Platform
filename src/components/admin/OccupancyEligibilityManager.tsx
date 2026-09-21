"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, ShieldCheck } from "lucide-react";
import { Guest, OccupancyEligibilityCheck, OccupancyEligibilityMethod } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { occupancyEligibilityMethodLabel, occupancyEligibilityStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

/** ZR-ENG-CLR-012 Section 9/23: manual-only Occupancy Eligibility review
 * queue (e.g. England's right-to-rent style check) -- opened here since
 * this MVP has no live government registry integration; every decision is
 * a human Verification Operations call, evidenced and audited like any
 * other admin decision on this platform. Section 12 deliberately keeps this
 * separate from IdentityVerification (a different verification domain with
 * a different purpose). */
// AC-09/AC-10: only these three are genuinely terminal (Section 8's "Check
// state" table) -- everything else routes to alternate/manual review and
// can still be re-decided.
const OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES = ["PASS", "FAIL_INELIGIBLE", "WAIVED_POLICY"];

type DecisionOutcome = "PASS" | "FAIL_INELIGIBLE" | "INCONCLUSIVE" | "TECHNICAL_ERROR" | "FRAUD_REVIEW" | "WAIVED_POLICY" | "SUSPENDED";

export function OccupancyEligibilityManager() {
  const [checks, setChecks] = useState<OccupancyEligibilityCheck[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [toast, setToast] = useState("");

  const [guests, setGuests] = useState<Guest[]>([]);

  const [openTarget, setOpenTarget] = useState(false);
  const [openGuestId, setOpenGuestId] = useState("");
  const [openJurisdiction, setOpenJurisdiction] = useState("England");
  const [openMethod, setOpenMethod] = useState<OccupancyEligibilityMethod>("MANUAL_DOCUMENT_CHECK");
  const [openShareCode, setOpenShareCode] = useState("");

  const [decideTarget, setDecideTarget] = useState<OccupancyEligibilityCheck | null>(null);
  const [decideResult, setDecideResult] = useState<DecisionOutcome>("PASS");
  const [decideNote, setDecideNote] = useState("");
  const [decideFollowUpDays, setDecideFollowUpDays] = useState("365");

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [checksData, guestsData] = await Promise.all([
        apiClientFetch<OccupancyEligibilityCheck[]>("/api/verification/occupancy-eligibility-checks"),
        apiClientFetch<Guest[]>("/api/guests"),
      ]);
      setChecks(checksData);
      setGuests(guestsData);
    } catch {
      showToast("Failed to load occupancy eligibility checks");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openOpenModal() {
    setOpenGuestId("");
    setOpenJurisdiction("England");
    setOpenMethod("MANUAL_DOCUMENT_CHECK");
    setOpenShareCode("");
    setOpenTarget(true);
  }

  async function submitOpen() {
    const guest = guests.find((g) => g.id === openGuestId);
    if (!guest) return showToast("Choose a renter");
    if (guest.partyId == null) return showToast("This guest has no linked Zoiko account, so a check can't be opened for them");
    const partyId = guest.partyId;
    if (!openJurisdiction.trim()) return showToast("Enter a jurisdiction");

    try {
      await apiClientFetch("/api/verification/occupancy-eligibility-checks", {
        method: "POST",
        body: JSON.stringify({
          partyId, jurisdictionCode: openJurisdiction.trim(), method: openMethod, shareCode: openShareCode.trim(),
        }),
      });
      showToast("Check opened.");
      setOpenTarget(false);
      await load();
    } catch {
      showToast("Failed to open this check");
    }
  }

  function openDecideModal(check: OccupancyEligibilityCheck) {
    setDecideTarget(check);
    setDecideResult("PASS");
    setDecideNote("");
    setDecideFollowUpDays("365");
  }

  async function submitDecide() {
    if (!decideTarget) return;
    setBusyId(decideTarget.id);
    try {
      await apiClientFetch(`/api/verification/occupancy-eligibility-checks/${decideTarget.id}/decide`, {
        method: "POST",
        body: JSON.stringify({
          resultStatus: decideResult, reasonNote: decideNote.trim(),
          followUpDays: decideResult === "PASS" && decideFollowUpDays ? Number(decideFollowUpDays) : null,
        }),
      });
      showToast("Decision recorded.");
      setDecideTarget(null);
      await load();
    } catch {
      showToast("Failed to record this decision");
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Occupancy Eligibility</h2>
        </div>
        <Button size="sm" variant="outline" onClick={openOpenModal}>
          Open a check
        </Button>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Jurisdiction-specific eligibility checks (e.g. England&apos;s right-to-rent style check) — never applied
        globally. Manual review only; every decision is audited.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : checks.length === 0 ? (
          <p className="text-sm text-slate-400">No pending occupancy eligibility checks.</p>
        ) : (
          checks.map((check) => (
            <div
              key={check.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  Party #{check.partyId} · {check.jurisdictionCode}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  {occupancyEligibilityMethodLabel[check.method] ?? check.method}
                  {check.shareCode && ` — code: ${check.shareCode}`}
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">Opened {formatDate(check.createdAt)}</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={occupancyEligibilityStatusTone[check.status] ?? "neutral"}>{check.status.replace(/_/g, " ")}</Badge>
                {!OCCUPANCY_ELIGIBILITY_TERMINAL_STATUSES.includes(check.status) && (
                  <Button size="sm" variant="primary" loading={busyId === check.id} onClick={() => openDecideModal(check)}>
                    Decide
                  </Button>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      <Modal open={openTarget} onClose={() => setOpenTarget(false)} title="Open an occupancy eligibility check">
        <div className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Renter</label>
            <select
              value={openGuestId}
              onChange={(e) => setOpenGuestId(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="">Choose a renter…</option>
              {guests.map((g) => (
                <option key={g.id} value={g.id} disabled={g.partyId == null}>
                  {g.name} — {g.email}
                  {g.partyId == null ? " (no Zoiko account)" : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Jurisdiction</label>
            <input
              value={openJurisdiction}
              onChange={(e) => setOpenJurisdiction(e.target.value)}
              placeholder="e.g. England"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Method</label>
            <select
              value={openMethod}
              onChange={(e) => setOpenMethod(e.target.value as OccupancyEligibilityMethod)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="MANUAL_DOCUMENT_CHECK">Manual document check</option>
              <option value="DIGITAL_SHARE_CODE">Digital share code</option>
            </select>
          </div>
          {openMethod === "DIGITAL_SHARE_CODE" && (
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Share code</label>
              <input
                value={openShareCode}
                onChange={(e) => setOpenShareCode(e.target.value)}
                placeholder="e.g. AB1-CD2-EF3"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpenTarget(false)}>
              Cancel
            </Button>
            <Button variant="primary" onClick={submitOpen}>
              Open check
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(decideTarget)} onClose={() => setDecideTarget(null)} title="Decide occupancy eligibility check">
        <div className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Result</label>
            <select
              value={decideResult}
              onChange={(e) => setDecideResult(e.target.value as typeof decideResult)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="PASS">Pass — eligible</option>
              <option value="FAIL_INELIGIBLE">Fail — not eligible</option>
              <option value="INCONCLUSIVE">Inconclusive — needs more evidence</option>
              <option value="TECHNICAL_ERROR">Technical error — lookup service unavailable</option>
              <option value="FRAUD_REVIEW">Fraud review — tampering/impersonation signal</option>
              <option value="WAIVED_POLICY">Waived by policy exception</option>
              <option value="SUSPENDED">Suspended — hold pending investigation</option>
            </select>
          </div>
          {(decideResult === "PASS" || decideResult === "WAIVED_POLICY") && (
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Follow-up in (days, optional)</label>
              <input
                type="number"
                min={0}
                value={decideFollowUpDays}
                onChange={(e) => setDecideFollowUpDays(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
          )}
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">
              Reason note{(decideResult === "PASS" || decideResult === "WAIVED_POLICY") && " (required)"}
            </label>
            <textarea
              value={decideNote}
              onChange={(e) => setDecideNote(e.target.value)}
              rows={3}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
            {(decideResult === "PASS" || decideResult === "WAIVED_POLICY") && (
              <p className="mt-1 text-xs text-slate-400">
                A PASS/waiver is recorded against the jurisdiction&apos;s current market policy version automatically --
                you only need to state the reason.
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setDecideTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="primary"
              loading={busyId === decideTarget?.id}
              disabled={(decideResult === "PASS" || decideResult === "WAIVED_POLICY") && !decideNote.trim()}
              onClick={submitDecide}
            >
              Record decision
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
