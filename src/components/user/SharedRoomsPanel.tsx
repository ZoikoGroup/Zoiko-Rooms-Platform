"use client";

import { useCallback, useEffect, useState } from "react";
import { BadgeCheck, ChevronDown, ChevronUp, ShieldAlert, Users } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, Toast, useToast } from "@/components/user/ui";
import { RentalTransactionRecord } from "@/components/user/RentalTransactionRecord";
import { SubletHostChatThread } from "@/components/user/RentalMessagesTab";
import { SubletRequest } from "@/lib/types";
import { subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { endCoTenancy, errorMessage, listCoTenancies, listSubletRequests } from "@/lib/user-api";

/** ADD_CO_TENANT shared rooms, from both sides: rooms the user shares as a
 *  co-tenant once the host has approved (with the renter's rent and payments,
 *  read-only), and co-tenants sharing the user's own room. Renders nothing when the user has none of these. */
/** mode "co_tenant": the rooms the user shares as the person added (shown in My Applications). mode "renter":
 *  the co-tenants sharing the user's own room (shown in Sublet Requests). */
export function SharedRoomsPanel({ mode }: { mode: "co_tenant" | "renter" }) {
  const { toast, showToast } = useToast();
  const [namingMe, setNamingMe] = useState<SubletRequest[]>([]);
  const [myCoTenants, setMyCoTenants] = useState<SubletRequest[]>([]);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    const [named, own] = await Promise.all([
      listCoTenancies().catch(() => [] as SubletRequest[]),
      listSubletRequests().catch(() => [] as SubletRequest[]),
    ]);
    setNamingMe(named);
    setMyCoTenants(own.filter((r) => r.arrangementType === "ADD_CO_TENANT" && r.sharedRoomActive));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function run(id: number, action: () => Promise<unknown>, success: string) {
    setBusyId(id);
    try {
      await action();
      showToast(success);
      await load();
    } catch (err) {
      showToast(errorMessage(err, "That didn't work. Please try again."), "error");
    } finally {
      setBusyId(null);
    }
  }

  const sharing = mode === "co_tenant" ? namingMe.filter((r) => r.sharedRoomActive) : [];
  const others = mode === "co_tenant" ? namingMe.filter((r) => !r.sharedRoomActive) : [];
  const ownCoTenants = mode === "renter" ? myCoTenants : [];

  if (!sharing.length && !ownCoTenants.length && !others.length) return null;

  return (
    <div className="mb-6 space-y-4">
      {sharing.map((r) => (
        <SharedRoomCard
          key={r.id}
          request={r}
          role="co_tenant"
          busy={busyId === r.id}
          onEnd={(reason) => run(r.id, () => endCoTenancy(r.id, reason), "You've left the shared room.")}
        />
      ))}

      {ownCoTenants.map((r) => (
        <SharedRoomCard
          key={r.id}
          request={r}
          role="renter"
          busy={busyId === r.id}
          onEnd={(reason) => run(r.id, () => endCoTenancy(r.id, reason), "Co-tenancy ended.")}
        />
      ))}

      {others.length > 0 && (
        <Card>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Co-tenancies you were named in</p>
          <ul className="mt-2 divide-y divide-slate-100 dark:divide-white/10">
            {others.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span className="text-slate-600 dark:text-slate-300">
                  {r.listingName || "Room"} — with {r.currentTenantName || "a renter"}
                </span>
                <Badge tone={subletRequestStatusTone[r.status] ?? "neutral"}>
                  {r.status === "pending_admin_review" ? "With host for review" : subletRequestStatusLabel[r.status] ?? r.status}
                </Badge>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Toast toast={toast} />
    </div>
  );
}

function VerifiedBadge({ verified }: { verified: boolean }) {
  return verified ? (
    <Badge tone="success">
      <BadgeCheck className="h-3 w-3" /> Verified
    </Badge>
  ) : (
    <Badge tone="warning">
      <ShieldAlert className="h-3 w-3" /> Not verified
    </Badge>
  );
}

function People({ request }: { request: SubletRequest }) {
  return (
    <div className="mt-3 space-y-1.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-primary-900 dark:text-white">{request.currentTenantName || "Renter"}</span>
        <span className="text-xs text-slate-400">renter · pays the host</span>
        <VerifiedBadge verified={request.currentTenantIdentityVerified} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold text-primary-900 dark:text-white">{request.proposedRenterName || "Co-tenant"}</span>
        <span className="text-xs text-slate-400">co-tenant · pays {request.currentTenantName || "the renter"}</span>
        <VerifiedBadge verified={request.proposedRenterIdentityVerified} />
      </div>
      {request.proposedMonthlyRent != null && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {`Co-tenant's monthly share: ${request.proposedMonthlyRent}, paid to ${request.currentTenantName || "the renter"} and confirmed by them. No separate deposit.`}
        </p>
      )}
    </div>
  );
}

export function SharedRoomCard({
  request,
  role,
  busy,
  onEnd,
}: {
  request: SubletRequest;
  role: "renter" | "co_tenant";
  busy: boolean;
  onEnd: (reason: string) => void;
}) {
  const [showRecord, setShowRecord] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [chat, setChat] = useState<"none" | "renter" | "host">("none");

  return (
    <Card className="!ring-emerald-200 dark:!ring-emerald-500/30">
      <div className={`flex flex-wrap items-start gap-3 ${role === "co_tenant" ? "justify-end" : "justify-between"}`}>
        <div className={role === "co_tenant" ? "hidden" : "min-w-0"}>
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300">
            <Users className="h-3.5 w-3.5" /> Shared room
          </p>
          <h3 className="mt-1 font-heading text-base font-bold text-primary-900 dark:text-white">
            {request.listingName || "Room"}
            {request.listingCity && <span className="font-normal text-slate-500"> · {request.listingCity}</span>}
          </h3>
          {request.decidedAt && (
            <p className="text-xs text-slate-500 dark:text-slate-400">Shared since {formatDate(request.decidedAt)}</p>
          )}
          <People request={request} />
        </div>
        {!confirming && (
          <Button variant="ghost" size="sm" className="w-full sm:w-auto" onClick={() => setConfirming(true)}>
            {role === "co_tenant" ? "Leave room" : "End co-tenancy"}
          </Button>
        )}
      </div>

      {confirming && (
        <div className="mt-3 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            {role === "co_tenant"
              ? "You'll stop sharing this room. The renter and the host are told."
              : "Your co-tenant will no longer share this room. They and the host are told."}
          </p>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            placeholder="Reason (optional)"
            className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-white/10 dark:bg-slate-900"
          />
          <div className="mt-2 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
            <Button variant="accent" size="sm" loading={busy} onClick={() => onEnd(reason)}>
              Confirm
            </Button>
          </div>
        </div>
      )}

      {role === "co_tenant" && (
        <div className="mt-4 border-t border-slate-100 pt-3 dark:border-white/10">
          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
            {request.hostThreadAvailable && (
              <button
                type="button"
                aria-expanded={chat === "host"}
                onClick={() => setChat((c) => (c === "host" ? "none" : "host"))}
                className={`rounded-full border px-4 py-2.5 sm:px-3 sm:py-1.5 text-xs font-bold transition-colors ${
                  chat === "host" ? "border-primary-700 bg-primary-700 text-white" : "border-slate-200 text-slate-600 hover:text-primary-700 dark:border-white/10 dark:text-slate-300"
                }`}
              >
                Message the host
              </button>
            )}
          </div>
          {chat === "host" && request.hostThreadAvailable && (
            <div className="mb-3">
              <SubletHostChatThread subletRequestId={request.id} side="occupant" counterpartName="your host" />
            </div>
          )}
          <button
            type="button"
            onClick={() => setShowRecord((v) => !v)}
            className="flex min-h-10 items-center gap-1.5 text-left text-sm font-semibold text-primary-700 dark:text-primary-300"
          >
            {showRecord ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            {showRecord ? "Hide" : "View"} the room&apos;s rent &amp; payments
          </button>
          {showRecord && (
            <div className="mt-3">
              <RentalTransactionRecord occupancyId={request.currentOccupancyId} role="co_tenant" coTenancyId={request.id} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
