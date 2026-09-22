"use client";

import { FormEvent, useEffect, useState } from "react";
import { CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { paymentConnectionStatusLabel, paymentConnectionStatusTone } from "@/lib/status";
import { PaymentConnection, PaymentRecipientAuthority, PaymentRecipientRelationshipType } from "@/lib/types";
import { formatDate } from "@/lib/utils";
import {
  confirmPaymentRecipientAuthorityChange,
  declareHostedPaymentRecipientAuthority,
  errorMessage,
  getHostedRoomPaymentConnection,
  listHostedRoomPaymentRecipientAuthorities,
  resendPaymentRecipientAuthorityChangeCode,
} from "@/lib/user-api";

/** ZR-PAY-LINK-003 Wireframe A/A.1: "Who should receive payments?" +
 *  "Payment recipient authority" -- a separate claim from listing
 *  authority. Only ever shown once a room exists (a listing without a
 *  linked room has nothing to set this up for). */
export function PaymentRecipientSetup({ roomId }: { roomId: number }) {
  const { toast, showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState<PaymentRecipientAuthority[]>([]);
  const [connection, setConnection] = useState<PaymentConnection | null>(null);
  const [showForm, setShowForm] = useState(false);

  function load() {
    setLoading(true);
    Promise.all([listHostedRoomPaymentRecipientAuthorities(roomId), getHostedRoomPaymentConnection(roomId)])
      .then(([authorities, conn]) => {
        setRecords(authorities);
        setConnection(conn);
      })
      .catch((err) => showToast(errorMessage(err, "Could not load payment recipient setup."), "error"))
      .finally(() => setLoading(false));
  }

  useEffect(load, [roomId]); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return <Loader label="Loading payment recipient setup" />;

  // Most recent row wins for display -- mirrors get_valid_payment_recipient_authority_for_room's
  // own "order_by(id.desc())" -- but an expired VERIFIED row is shown as
  // expired, never silently treated as still current.
  const now = Date.now();
  const current = [...records].sort((a, b) => b.id - a.id)[0] ?? null;
  const currentIsLiveVerified =
    current?.status === "verified" && (!current.expiresAt || new Date(current.expiresAt).getTime() > now);

  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500 dark:text-slate-400">
        This determines who is authorized to receive rent and deposit payments for this room -- a separate check from
        being allowed to list the property.
      </p>

      {connection && <PaymentConnectionBanner connection={connection} />}

      {current && !showForm && (
        <Card
          className={
            currentIsLiveVerified
              ? "!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20"
              : current.status === "pending" || current.status === "pending_step_up"
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
                ? "Verified payment recipient"
                : current.status === "pending_step_up"
                  ? "Confirm this change to continue"
                  : current.status === "pending"
                    ? "Awaiting admin verification"
                    : current.status === "revoked"
                      ? "Recipient authority revoked"
                      : current.status === "failed"
                        ? "Verification failed"
                        : "Recipient authority expired"}
            </p>
          </div>
          <dl className="mt-3 space-y-1.5 text-sm">
            <div className="flex justify-between">
              <dt className="text-slate-500 dark:text-slate-400">Relationship</dt>
              <dd className="font-semibold text-slate-700 dark:text-slate-200">{current.relationshipType}</dd>
            </div>
            {current.expiresAt && (
              <div className="flex justify-between">
                <dt className="text-slate-500 dark:text-slate-400">{currentIsLiveVerified ? "Valid until" : "Expired"}</dt>
                <dd className="font-semibold text-slate-700 dark:text-slate-200">{formatDate(current.expiresAt)}</dd>
              </div>
            )}
          </dl>
          {current.status !== "pending_step_up" && (
            <div className="mt-4">
              <Button size="sm" variant="outline" onClick={() => setShowForm(true)}>
                {currentIsLiveVerified ? "Change recipient" : "Submit again"}
              </Button>
            </div>
          )}
        </Card>
      )}

      {current?.status === "pending_step_up" && (
        <ConfirmChangeForm
          roomId={roomId}
          authority={current}
          onConfirmed={() => {
            showToast("Change confirmed -- submitted for admin verification.");
            load();
          }}
        />
      )}

      {(!current || showForm) && (
        <DeclareForm
          roomId={roomId}
          onCancel={current ? () => setShowForm(false) : undefined}
          onSubmitted={(record) => {
            setShowForm(false);
            if (record.status === "pending_step_up") {
              showToast("Check your email for a confirmation code.");
            } else {
              showToast("Submitted for admin verification.");
            }
            load();
          }}
        />
      )}

      <Toast toast={toast} />
    </div>
  );
}

/** ZR-PAY-LINK-003 Section 3.1/23: the consolidated recipient+destination
 *  status -- what the authority-status card above can't show on its own,
 *  since a room can have a verified recipient and still have nowhere for
 *  rent to actually go (RECIPIENT_SETUP_REQUIRED). */
function PaymentConnectionBanner({ connection }: { connection: PaymentConnection }) {
  const description =
    connection.state === "ACTIVE"
      ? "Rent and deposit payments can be collected for this room."
      : connection.state === "DRAFT"
        ? "No payment recipient has been declared yet. No payment destination is available until you do."
        : connection.state === "PENDING_VERIFICATION"
          ? "A Zoiko admin still needs to verify the payment recipient or destination before payments can start."
          : connection.state === "RECIPIENT_SETUP_REQUIRED"
            ? "The recipient is verified, but no payment destination has been set up yet -- add one below."
            : "Payments are suspended for this room. Review the recipient authority and payment destination.";

  return (
    <Card>
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Payment connection</p>
        <Badge tone={paymentConnectionStatusTone[connection.state]}>{paymentConnectionStatusLabel[connection.state]}</Badge>
      </div>
      <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">{description}</p>
      <dl className="mt-3 space-y-1.5 text-sm">
        <div className="flex justify-between">
          <dt className="text-slate-500 dark:text-slate-400">Payment destination</dt>
          <dd className="font-semibold text-slate-700 dark:text-slate-200">
            {connection.destinationMethod
              ? `${connection.destinationMethod.replace("_", " ")} (${connection.destinationAccountIdentifierMasked})`
              : "Not configured yet"}
          </dd>
        </div>
      </dl>
    </Card>
  );
}

/** ZR-PAY-LINK-003 Section 14.1: the step-up confirmation for a recipient
 *  CHANGE -- a code mailed to the submitter's own account, proving they
 *  control it before the change even reaches the admin verification queue.
 *  Never shown for a room's first-ever declaration (that goes straight to
 *  "pending", no code involved). */
function ConfirmChangeForm({
  roomId,
  authority,
  onConfirmed,
}: {
  roomId: number;
  authority: PaymentRecipientAuthority;
  onConfirmed: () => void;
}) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [error, setError] = useState("");
  const [resent, setResent] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await confirmPaymentRecipientAuthorityChange(roomId, authority.id, code.trim());
      onConfirmed();
    } catch (err) {
      setError(errorMessage(err, "Could not confirm this change."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleResend() {
    setError("");
    setResending(true);
    try {
      await resendPaymentRecipientAuthorityChangeCode(roomId, authority.id);
      setResent(true);
    } catch (err) {
      setError(errorMessage(err, "Could not resend the code."));
    } finally {
      setResending(false);
    }
  }

  return (
    <Card>
      <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">Confirm this change</p>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        We emailed a 6-digit code to your account to confirm this change to who receives rent for this room.
      </p>
      <form onSubmit={handleSubmit} className="mt-4 space-y-4">
        {error && (
          <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {error}
          </p>
        )}
        <Field label="Confirmation code *">
          <input
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className={inputClass}
            inputMode="numeric"
            maxLength={6}
            autoComplete="one-time-code"
          />
        </Field>
        <div className="flex items-center justify-between gap-2">
          <Button type="button" variant="ghost" size="sm" loading={resending} onClick={handleResend}>
            {resent ? "Code resent" : "Resend code"}
          </Button>
          <Button type="submit" loading={submitting}>
            Confirm change
          </Button>
        </div>
      </form>
    </Card>
  );
}

function DeclareForm({
  roomId,
  onCancel,
  onSubmitted,
}: {
  roomId: number;
  onCancel?: () => void;
  onSubmitted: (record: PaymentRecipientAuthority) => void;
}) {
  const [who, setWho] = useState<"self" | "other">("self");
  const [relationshipType, setRelationshipType] = useState<PaymentRecipientRelationshipType>("OWNER");
  const [recipientPartyId, setRecipientPartyId] = useState("");
  const [evidenceRef, setEvidenceRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setRelationshipType(who === "self" ? "OWNER" : "AGENT");
  }, [who]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (who === "other" && !recipientPartyId.trim()) {
      setError("Enter the recipient's party ID.");
      return;
    }
    setSubmitting(true);
    try {
      const record = await declareHostedPaymentRecipientAuthority(roomId, {
        recipientPartyId: who === "other" ? Number(recipientPartyId) : undefined,
        relationshipType,
        evidenceRef: evidenceRef.trim(),
      });
      onSubmitted(record);
    } catch (err) {
      setError(errorMessage(err, "Could not submit payment recipient authority."));
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

      <Field label="Who should receive payments? *">
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input type="radio" checked={who === "self"} onChange={() => setWho("self")} className="h-4 w-4" />
            Me / the property owner
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
            <input type="radio" checked={who === "other"} onChange={() => setWho("other")} className="h-4 w-4" />
            An authorized agent, property manager or other authorized recipient
          </label>
        </div>
      </Field>

      {who === "other" && (
        <>
          <Field label="Recipient party ID *" hint="The Zoiko account ID of the authorized agent/manager.">
            <input
              required
              value={recipientPartyId}
              onChange={(e) => setRecipientPartyId(e.target.value)}
              className={inputClass}
              inputMode="numeric"
            />
          </Field>
          <Field label="Relationship *">
            <select
              value={relationshipType}
              onChange={(e) => setRelationshipType(e.target.value as PaymentRecipientRelationshipType)}
              className={inputClass}
            >
              <option value="AGENT">Authorized agent</option>
              <option value="MANAGER">Property manager</option>
              <option value="OTHER">Other authorized recipient</option>
            </select>
          </Field>
        </>
      )}

      <Field label="Evidence reference *" hint="e.g. ID document, agency agreement, or uploaded document reference.">
        <input required value={evidenceRef} onChange={(e) => setEvidenceRef(e.target.value)} className={inputClass} />
      </Field>

      <p className="flex items-start gap-1.5 text-xs text-slate-400">
        <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> This is separate from authority to
        list the property. A Zoiko admin verifies it before it takes effect.
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
