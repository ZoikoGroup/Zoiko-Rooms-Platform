"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Clock, Key } from "lucide-react";
import {
  confirmHostedMoveIn,
  confirmHostedPossessionDelivered,
  errorMessage,
  getHostedMoveInEligibility,
  getHostedRentalTransactionRecord,
  prepareHostedHandover,
  hostProceedDespiteDispute,
} from "@/lib/user-api";
import type { ToastTone } from "@/components/user/ui";

/** Host side of the move-in activation gate: mark handover ready, record that
 *  keys/room were handed over, then confirm move-in (once the renter has
 *  confirmed receipt). Shared by the Properties & Rooms and Listings drawers. */
export function HostMoveInSteps({
  occupancyId,
  showToast,
  onMovedIn,
}: {
  occupancyId: number;
  showToast: (message: string, tone?: ToastTone) => void;
  onMovedIn: () => void;
}) {
  const [busy, setBusy] = useState<"prepare" | "possession" | "confirm" | "proceed" | null>(null);
  const [disputeHold, setDisputeHold] = useState(false);
  const [eventTypes, setEventTypes] = useState<Set<string>>(new Set());
  const [loadingEvents, setLoadingEvents] = useState(true);

  const refreshEvents = useCallback(async () => {
    try {
      const record = await getHostedRentalTransactionRecord(occupancyId);
      const types = new Set((record.handoverEvents || []).map((e) => e.eventType));
      setEventTypes(types);
    } catch {
      // Non-blocking fallback
    } finally {
      setLoadingEvents(false);
    }
  }, [occupancyId]);

  useEffect(() => {
    refreshEvents();
  }, [refreshEvents]);

  const hasHandoverReady = eventTypes.has("HANDOVER_READY");
  const hasPossessionDelivered = eventTypes.has("POSSESSION_DELIVERED");
  const hasRenterReceipt = eventTypes.has("RENTER_RECEIPT");
  const allReady = hasHandoverReady && hasPossessionDelivered && hasRenterReceipt;

  async function run(step: "prepare" | "possession" | "confirm") {
    setBusy(step);
    try {
      if (step === "prepare") {
        await prepareHostedHandover(occupancyId);
        setEventTypes((prev) => new Set(prev).add("HANDOVER_READY"));
        showToast("Marked handover ready.");
      } else if (step === "possession") {
        await confirmHostedPossessionDelivered(occupancyId);
        setEventTypes((prev) => new Set(prev).add("POSSESSION_DELIVERED"));
        showToast("Keys & room handover recorded. The renter can now confirm receipt.");
      } else {
        const eligibility = await getHostedMoveInEligibility(occupancyId);
        if (!eligibility.eligible) {
          showToast(`Not ready for move-in yet: ${eligibility.reasons.join("; ")}`, "error");
          return;
        }
        await confirmHostedMoveIn(occupancyId);
        showToast("Move-in confirmed -- the tenancy is now active.");
        onMovedIn();
      }
    } catch (err) {
      const message = errorMessage(err, "Could not complete this move-in step.");
      if (message.includes("OPEN_OCCUPANCY_DISPUTE")) {
        setDisputeHold(true);
        showToast("Move-in is on hold while a dispute on this rental is open.", "error");
      } else {
        showToast(message, "error");
      }
    } finally {
      setBusy(null);
    }
  }

  async function proceedDespiteDispute() {
    setBusy("proceed");
    try {
      await hostProceedDespiteDispute(occupancyId);
      showToast("Recorded. Move-in can go ahead once the renter agrees too.");
    } catch (err) {
      showToast(errorMessage(err, "Could not record your agreement."), "error");
    } finally {
      setBusy(null);
    }
  }

  const outline =
    "w-full h-10 px-4 rounded-full border border-[#0E2F73] text-[#0E2F73] dark:text-white hover:bg-[#EAF0FB] dark:hover:bg-slate-800 text-xs font-bold font-heading flex items-center justify-center gap-2 transition cursor-pointer disabled:opacity-60";

  return (
    <div className="space-y-3 rounded-2xl border border-[#F5C2C2] bg-[#FFF8F8] dark:border-red-900/40 dark:bg-red-950/10 p-4">
      <div className="flex items-center gap-2 text-xs font-extrabold text-[#0E2F73] dark:text-white font-heading">
        <Key className="size-4" /> Move-in activation steps
      </div>
      <p className="text-[11px] text-slate-500 dark:text-slate-400">
        Complete the handover steps below. Once both you and the renter confirm key handover, move-in can be confirmed to activate the rental.
      </p>

      {/* Step 1: Handover ready */}
      {hasHandoverReady ? (
        <div className="flex items-center justify-between p-2.5 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 text-xs text-emerald-800 dark:text-emerald-300 font-semibold">
          <span className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            1. Handover marked ready
          </span>
          <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-700 bg-emerald-100 dark:bg-emerald-900/50 px-2 py-0.5 rounded-full">
            Completed
          </span>
        </div>
      ) : (
        <button type="button" disabled={busy !== null || loadingEvents} onClick={() => run("prepare")} className={outline}>
          {busy === "prepare" ? "Saving…" : "1. Mark handover ready"}
        </button>
      )}

      {/* Step 2: Keys handed over */}
      {hasPossessionDelivered ? (
        <div className="flex items-center justify-between p-2.5 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 text-xs text-emerald-800 dark:text-emerald-300 font-semibold">
          <span className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            2. Keys &amp; room handover recorded
          </span>
          <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-700 bg-emerald-100 dark:bg-emerald-900/50 px-2 py-0.5 rounded-full">
            Completed
          </span>
        </div>
      ) : (
        <button
          type="button"
          disabled={busy !== null || !hasHandoverReady || loadingEvents}
          onClick={() => run("possession")}
          className={outline}
        >
          {busy === "possession" ? "Saving…" : "2. Confirm keys & room handed over"}
        </button>
      )}

      {/* Step 3: Renter key receipt */}
      {hasRenterReceipt ? (
        <div className="flex items-center justify-between p-2.5 rounded-xl bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 text-xs text-emerald-800 dark:text-emerald-300 font-semibold">
          <span className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            3. Renter confirmed key receipt
          </span>
          <span className="text-[10px] font-bold uppercase tracking-wider text-emerald-700 bg-emerald-100 dark:bg-emerald-900/50 px-2 py-0.5 rounded-full">
            Confirmed
          </span>
        </div>
      ) : (
        <div className="flex items-center justify-between p-2.5 rounded-xl bg-amber-50 dark:bg-amber-950/30 border border-amber-200 dark:border-amber-800 text-xs text-amber-800 dark:text-amber-300 font-semibold">
          <span className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-amber-600 shrink-0" />
            3. Renter key receipt confirmation
          </span>
          <span className="text-[11px] font-medium text-amber-700 dark:text-amber-400">
            {hasPossessionDelivered ? "Waiting for renter" : "Awaiting step 2"}
          </span>
        </div>
      )}

      {/* Step 4: Final Move-in Confirmation */}
      <button
        type="button"
        disabled={busy !== null || !allReady || loadingEvents}
        onClick={() => run("confirm")}
        className="w-full h-11 px-4 rounded-full bg-[#05603A] hover:bg-[#044d2f] text-white text-xs font-bold font-heading flex items-center justify-center gap-2 shadow-sm transition cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {busy === "confirm" ? "Confirming Move-in…" : "Confirm Move-in & Activate Tenancy"}
      </button>
      {!allReady && (
        <p className="text-[10.5px] text-center text-slate-500 dark:text-slate-400">
          Complete previous steps to unlock move-in confirmation.
        </p>
      )}

      {disputeHold && (
        <div className="space-y-1.5 rounded-xl border border-amber-200 bg-amber-50 p-2.5 text-[11px] text-amber-900">
          <p>
            A dispute on this rental is still open, so move-in waits until it&apos;s resolved — or goes ahead now if you and
            the renter both agree.
          </p>
          <button type="button" disabled={busy !== null} onClick={proceedDespiteDispute} className={outline}>
            {busy === "proceed" ? "Saving…" : "Agree to move in anyway"}
          </button>
        </div>
      )}
    </div>
  );
}

