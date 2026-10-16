"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { BadgeCheck, ChevronDown, ChevronUp, ShieldAlert, Users } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, Toast, useToast } from "@/components/user/ui";
import { RentalTransactionRecord } from "@/components/user/RentalTransactionRecord";
import { SubletRequest } from "@/lib/types";
import { subletRequestStatusLabel, subletRequestStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { endCoTenancy, errorMessage, listCoTenancies, listSubletRequests, respondToCoTenancy } from "@/lib/user-api";

/** ADD_CO_TENANT shared rooms, from both sides: invitations the user has been
 *  named in (accept/decline), rooms they share as a co-tenant (with the
 *  renter's rent and payments, read-only), and co-tenants sharing the user's
 *  own room. Renders nothing when the user has none of these. */
export function SharedRoomsPanel() {
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

  const invitations = namingMe.filter((r) => r.status === "awaiting_co_tenant");
  const sharing = namingMe.filter((r) => r.sharedRoomActive);
  const others = namingMe.filter((r) => r.status !== "awaiting_co_tenant" && !r.sharedRoomActive);

  if (!invitations.length && !sharing.length && !myCoTenants.length && !others.length) return null;

  return (
    <div className="mb-6 space-y-4">
      {invitations.map((r) => (
        <Card key={r.id} className="!ring-amber-200 dark:!ring-amber-500/30">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300">Invitation to share a room</p>
              <h3 className="mt-1 font-heading text-base font-bold text-primary-900 dark:text-white">
                {r.currentTenantName || "A renter"} invited you to share {r.listingName || "their room"}
              </h3>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {[r.listingCity, r.roomType].filter(Boolean).join(" · ")}
                {r.proposedStartDate && ` · from ${formatDate(r.proposedStartDate)}`}
                {r.proposedEndDate && ` to ${formatDate(r.proposedEndDate)}`}
              </p>
              <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
                You&apos;d live in the room with them and pay your share to them directly. They stay the tenant and keep
                paying the host. Once you accept, the host reviews it.
              </p>
              {r.reason && <p className="mt-1 text-xs italic text-slate-500">&ldquo;{r.reason}&rdquo;</p>}
              <People request={r} />
              {!r.proposedRenterIdentityVerified && (
                <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                  Verify your identity before accepting.{" "}
                  <Link href="/account/identity" className="font-semibold underline">Verify now</Link>
                </p>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={busyId === r.id}
                onClick={() => run(r.id, () => respondToCoTenancy(r.id, false), "Invitation declined.")}
              >
                Decline
              </Button>
              <Button
                size="sm"
                loading={busyId === r.id}
                disabled={!r.proposedRenterIdentityVerified}
                onClick={() => run(r.id, () => respondToCoTenancy(r.id, true), "Accepted — the host will review it next.")}
              >
                Accept
              </Button>
            </div>
          </div>
        </Card>
      ))}

      {sharing.map((r) => (
        <SharedRoomCard
          key={r.id}
          request={r}
          role="co_tenant"
          busy={busyId === r.id}
          onEnd={(reason) => run(r.id, () => endCoTenancy(r.id, reason), "You've left the shared room.")}
        />
      ))}

      {myCoTenants.map((r) => (
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
    </div>
  );
}

function SharedRoomCard({
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

  return (
    <Card className="!ring-emerald-200 dark:!ring-emerald-500/30">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
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
          <Button variant="ghost" size="sm" onClick={() => setConfirming(true)}>
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
          <div className="mt-2 flex justify-end gap-2">
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
          <button
            type="button"
            onClick={() => setShowRecord((v) => !v)}
            className="flex items-center gap-1.5 text-sm font-semibold text-primary-700 dark:text-primary-300"
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
