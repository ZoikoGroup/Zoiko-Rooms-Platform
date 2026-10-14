"use client";

import { useState } from "react";
import { Key } from "lucide-react";
import {
  confirmHostedMoveIn,
  confirmHostedPossessionDelivered,
  errorMessage,
  getHostedMoveInEligibility,
  prepareHostedHandover,
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
  const [busy, setBusy] = useState<"prepare" | "possession" | "confirm" | null>(null);

  async function run(step: "prepare" | "possession" | "confirm") {
    setBusy(step);
    try {
      if (step === "prepare") {
        await prepareHostedHandover(occupancyId);
        showToast("Marked handover ready.");
      } else if (step === "possession") {
        await confirmHostedPossessionDelivered(occupancyId);
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
      showToast(errorMessage(err, "Could not complete this move-in step."), "error");
    } finally {
      setBusy(null);
    }
  }

  const outline =
    "w-full h-10 px-4 rounded-full border border-[#0E2F73] text-[#0E2F73] dark:text-white hover:bg-[#EAF0FB] dark:hover:bg-slate-800 text-xs font-bold font-heading flex items-center justify-center gap-2 transition cursor-pointer disabled:opacity-60";

  return (
    <div className="space-y-2 rounded-2xl border border-[#F5C2C2] bg-[#FFF8F8] dark:border-red-900/40 dark:bg-red-950/10 p-3.5">
      <div className="flex items-center gap-2 text-xs font-extrabold text-[#0E2F73] dark:text-white font-heading">
        <Key className="size-4" /> Move-in pending
      </div>
      <p className="text-[11px] text-slate-500 dark:text-slate-400">
        Mark the handover ready, hand over the keys and room, then confirm move-in once the renter has confirmed
        receipt.
      </p>
      <button type="button" disabled={busy !== null} onClick={() => run("prepare")} className={outline}>
        {busy === "prepare" ? "Saving…" : "1. Mark handover ready"}
      </button>
      <button type="button" disabled={busy !== null} onClick={() => run("possession")} className={outline}>
        {busy === "possession" ? "Saving…" : "2. Confirm keys & room handed over"}
      </button>
      <button
        type="button"
        disabled={busy !== null}
        onClick={() => run("confirm")}
        className="w-full h-10 px-4 rounded-full bg-[#05603A] hover:bg-[#044d2f] text-white text-xs font-bold font-heading flex items-center justify-center gap-2 shadow-sm transition cursor-pointer disabled:opacity-60"
      >
        {busy === "confirm" ? "Confirming…" : "3. Confirm move-in"}
      </button>
    </div>
  );
}
