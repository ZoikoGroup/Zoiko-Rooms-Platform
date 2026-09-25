"use client";

import { FormEvent, useEffect, useState } from "react";
import { CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { AuthorityRecord, AuthorityRelationshipType } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import { declareHostedAuthorityRecord, errorMessage, listHostedRoomAuthorityRecords } from "@/lib/user-api";

const LIVE_STATUSES = new Set(["pending", "verified", "expiring"]);

/** "Authority to list this property" -- the right (owner, agent, or
 *  manager) to list this specific room, a separate claim from both
 *  identity (who the lister is) and property verification (is the
 *  property/address itself real). Same gap PropertyVerificationManager
 *  fixed: declareHostedAuthorityRecord/listHostedRoomAuthorityRecords
 *  already existed in the backend and user-api.ts, but nothing in the UI
 *  ever called them -- previously only reachable once, inline in
 *  ListARoomWizard's review step, with no way back in for a room created
 *  before that step existed or where it was skipped. */
export function AuthorityRecordManager({ roomId }: { roomId: number }) {
  const { toast, showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState<AuthorityRecord[]>([]);
  const [showForm, setShowForm] = useState(false);

  function load() {
    setLoading(true);
    listHostedRoomAuthorityRecords(roomId)
      .then(setRecords)
      .catch((err) => showToast(errorMessage(err, "Could not load authority record status."), "error"))
      .finally(() => setLoading(false));
  }

  useEffect(load, [roomId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <Loader label="Loading authority record status" />;

  const current = [...records].sort((a, b) => b.id - a.id)[0] ?? null;
  const now = Date.now();
  const currentIsLiveVerified =
    current?.status === "verified" && (!current.expiresAt || new Date(current.expiresAt).getTime() > now);

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Confirm your right to list this room -- as the owner, an authorized agent, or a property manager. A Zoiko
        admin reviews this separately from your identity and property verifications.
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
                ? "Authority verified"
                : current.status === "pending"
                  ? "Awaiting admin verification"
                  : current.status === "review_required"
                    ? "Needs a closer admin review"
                    : current.status === "conflict"
                      ? "Conflicts with another claim on this room"
                      : current.status === "revoked"
                        ? "Authority revoked"
                        : current.status === "failed"
                          ? "Not approved"
                          : "Authority expired"}
            </p>
          </div>
          <dl className="mt-3 space-y-1.5 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500 dark:text-slate-400">Relationship</dt>
              <dd className="font-semibold text-slate-700 dark:text-slate-200">{current.relationshipType ?? "--"}</dd>
            </div>
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
          {!LIVE_STATUSES.has(current.status) || !currentIsLiveVerified ? (
            <div className="mt-4">
              <Button size="sm" variant="outline" onClick={() => setShowForm(true)}>
                {currentIsLiveVerified ? "Submit again" : "Re-submit"}
              </Button>
            </div>
          ) : null}
        </Card>
      )}

      {(!current || showForm) && (
        <DeclareForm
          roomId={roomId}
          onCancel={current ? () => setShowForm(false) : undefined}
          onSubmitted={() => {
            setShowForm(false);
            showToast("Submitted for admin verification.");
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
  onSubmitted: (record: AuthorityRecord) => void;
}) {
  const [relationshipType, setRelationshipType] = useState<AuthorityRelationshipType>("OWNER");
  const [evidenceRef, setEvidenceRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (!evidenceRef.trim()) {
      setError("Enter an evidence reference.");
      return;
    }
    setSubmitting(true);
    try {
      const record = await declareHostedAuthorityRecord(roomId, { relationshipType, evidenceRef: evidenceRef.trim() });
      onSubmitted(record);
    } catch (err) {
      setError(errorMessage(err, "Could not submit authority evidence."));
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

      <Field label="Your relationship to this property *">
        <select
          value={relationshipType}
          onChange={(e) => setRelationshipType(e.target.value as AuthorityRelationshipType)}
          className={inputClass}
        >
          <option value="OWNER">Owner</option>
          <option value="AGENT">Agent</option>
          <option value="MANAGER">Manager</option>
        </select>
      </Field>

      <Field label="Evidence reference *" hint="e.g. title deed, lease, or NOC reference">
        <input required value={evidenceRef} onChange={(e) => setEvidenceRef(e.target.value)} className={inputClass} />
      </Field>

      <p className="flex items-start gap-1.5 text-xs text-slate-400">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> This is separate from your identity
        and property verifications. A Zoiko admin reviews it before this room can be listed.
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
