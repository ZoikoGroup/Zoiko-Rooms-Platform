"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Flag, MessageSquare, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { errorMessage } from "@/lib/user-api";
import { formatDate } from "@/lib/utils";
import {
  consentToShareContact,
  listMyRoomRequests,
  reportExternalOpportunity,
  sendRoomRequestMessage,
  type ReportReason,
  type RenterRequestView,
} from "@/lib/external-search";

const RESPONSE_TEXT: Record<string, string> = {
  AWAITING_RESPONSE: "Waiting for the provider",
  ACCEPTED: "Provider accepted",
  DECLINED: "Provider declined",
  NO_RESPONSE: "No response",
};

/** ZR-AI-SEARCH-001 Sections 9 and 11: the renter's requests for Zoiko Rooms
 *  to contact external providers -- status, the Zoiko-mediated conversation,
 *  sharing contact details (only when both sides agree) and reporting a lead
 *  that is stale or wrong. */
export default function RoomRequestsPage() {
  const [items, setItems] = useState<RenterRequestView[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    listMyRoomRequests().then(setItems).catch((err) => setError(errorMessage(err, "Could not load your requests.")));
  }, []);
  useEffect(load, [load]);

  const replace = (next: RenterRequestView) =>
    setItems((prev) => (prev ?? []).map((it) => (it.outreachId === next.outreachId ? next : it)));

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Room requests</h1>
        <p className="text-sm text-slate-500">Rooms from outside Zoiko Rooms that you asked us to enquire about.</p>
      </div>
      {error && <p className="text-sm text-accent-700" role="alert">{error}</p>}
      {items === null && !error && <p className="text-sm text-slate-400" role="status">Loading...</p>}
      {items?.length === 0 && (
        <p className="rounded-2xl bg-white p-5 text-sm text-slate-500 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
          No requests yet. When a search finds rooms outside Zoiko Rooms, you can ask us to contact the provider.
        </p>
      )}
      {items?.map((item) => <RequestCard key={item.outreachId} item={item} onChange={replace} />)}
    </div>
  );
}

function RequestCard({ item, onChange }: { item: RenterRequestView; onChange: (v: RenterRequestView) => void }) {
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reported, setReported] = useState(false);
  const [reason, setReason] = useState<ReportReason>("STALE");

  async function run(fn: () => Promise<RenterRequestView | void>) {
    setBusy(true);
    setError("");
    try {
      const next = await fn();
      if (next) onChange(next);
    } catch (err) {
      setError(errorMessage(err, "Something went wrong."));
    } finally {
      setBusy(false);
    }
  }

  function send(e: FormEvent) {
    e.preventDefault();
    if (!message.trim()) return;
    run(async () => {
      const next = await sendRoomRequestMessage(item.outreachId, message.trim());
      setMessage("");
      return next;
    });
  }

  return (
    <article className="space-y-3 rounded-2xl bg-white p-5 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-semibold text-slate-800 dark:text-slate-100">Room in {item.approxLocation ?? "an external listing"}</p>
          <p className="text-xs text-slate-500">
            Requested {item.requestedAt ? formatDate(item.requestedAt) : ""} · {RESPONSE_TEXT[item.providerResponse] ?? item.providerResponse}
            {item.providerName ? ` · ${item.providerName}` : ""}
          </p>
        </div>
        <span className="rounded-full bg-amber-100 px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-amber-800 dark:bg-amber-500/20 dark:text-amber-300">
          External · Not verified by Zoiko Rooms
        </span>
      </div>

      {item.canMessage ? (
        <div className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
            <MessageSquare className="h-4 w-4" aria-hidden="true" /> Messages with the provider
          </h2>
          <ul className="space-y-2">
            {item.messages.map((m) => (
              <li key={m.id} className={`rounded-xl px-3 py-2 text-sm ${m.from === "you" ? "ml-8 bg-primary-50 dark:bg-primary-500/10" : "mr-8 bg-slate-100 dark:bg-slate-800"}`}>
                <span className="block text-[11px] text-slate-400">{m.from === "you" ? "You" : "Provider"}</span>
                {m.body}
              </li>
            ))}
            {item.messages.length === 0 && <li className="text-xs text-slate-400">No messages yet.</li>}
          </ul>
          <form className="flex gap-2" onSubmit={send}>
            <input value={message} onChange={(e) => setMessage(e.target.value)} maxLength={5000} placeholder="Write a message"
                   className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800" />
            <Button type="submit" disabled={busy || !message.trim()}>Send</Button>
          </form>
          <p className="flex items-start gap-1.5 text-[11px] text-slate-400">
            <ShieldAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            The provider is not verified by Zoiko Rooms. Never pay a deposit or rent before viewing and checking a tenancy agreement.
          </p>
          {item.release.available && (
            <div className="rounded-xl border border-slate-200 p-3 text-sm dark:border-slate-700">
              {item.release.released ? (
                <p>Provider&apos;s contact: <strong>{item.release.contact}</strong></p>
              ) : item.release.youConsented ? (
                <p className="text-slate-600 dark:text-slate-300">You agreed to share contact details. Waiting for the provider.</p>
              ) : (
                <Button variant="ghost" disabled={busy} onClick={() => run(() => consentToShareContact(item.outreachId))}>
                  Agree to share contact details
                </Button>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="text-sm text-slate-500">
          {item.providerResponse === "DECLINED"
            ? "The provider declined. Try another room."
            : "We'll let you know when the provider responds. Your contact details are not shared."}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3 text-xs dark:border-slate-800">
        {reported ? (
          <span className="text-slate-500">Thanks, we&apos;ll review this listing.</span>
        ) : (
          <>
            <Flag className="h-3.5 w-3.5 text-slate-400" aria-hidden="true" />
            <label htmlFor={`report-${item.outreachId}`} className="text-slate-500">Report this listing:</label>
            <select id={`report-${item.outreachId}`} value={reason} onChange={(e) => setReason(e.target.value as ReportReason)}
                    className="rounded-lg border border-slate-300 px-2 py-1 dark:border-slate-700 dark:bg-slate-800">
              <option value="STALE">No longer available</option>
              <option value="INACCURATE">Details are wrong</option>
              <option value="SUSPICIOUS">Looks suspicious</option>
              <option value="OTHER">Other</option>
            </select>
            <Button variant="ghost" disabled={busy}
                    onClick={() => run(async () => { await reportExternalOpportunity(item.opportunityId, reason); setReported(true); })}>
              Send report
            </Button>
          </>
        )}
        {error && <span className="text-accent-700" role="alert">{error}</span>}
      </div>
    </article>
  );
}
