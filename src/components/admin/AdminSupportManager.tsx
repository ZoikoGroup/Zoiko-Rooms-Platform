"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, FileText, RefreshCw } from "lucide-react";
import {
  AdminSupportTicket,
  adminSupportAttachmentUrl,
  listAdminSupportTickets,
  replyAsSupport,
  setSupportTicketStatus,
} from "@/lib/contact-email";
import { errorMessage } from "@/lib/user-api";
import type { SupportTicketStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<SupportTicketStatus, string> = {
  SUBMITTED: "Submitted",
  IN_REVIEW: "In review",
  ACTION_REQUIRED: "Action required (user)",
  RESOLVED: "Resolved",
  CLOSED: "Closed",
};

const STATUS_TONE: Record<SupportTicketStatus, string> = {
  SUBMITTED: "bg-blue-50 text-blue-800 border-blue-200 dark:bg-blue-950/30 dark:text-blue-300 dark:border-blue-900",
  IN_REVIEW: "bg-indigo-50 text-indigo-800 border-indigo-200 dark:bg-indigo-950/30 dark:text-indigo-300 dark:border-indigo-900",
  ACTION_REQUIRED: "bg-amber-50 text-amber-800 border-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:border-amber-900",
  RESOLVED: "bg-emerald-50 text-emerald-800 border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:border-emerald-900",
  CLOSED: "bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700",
};

type Filter = "open" | SupportTicketStatus | "all";

const inputCls =
  "w-full rounded-xl border border-slate-300 bg-white p-2.5 text-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";

export function AdminSupportManager() {
  const [tickets, setTickets] = useState<AdminSupportTicket[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<Filter>("open");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const [reply, setReply] = useState("");
  const [nextStatus, setNextStatus] = useState<Exclude<SupportTicketStatus, "SUBMITTED">>("IN_REVIEW");
  const [statusNote, setStatusNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setTickets(await listAdminSupportTickets());
    } catch (err) {
      setError(errorMessage(err, "Could not load support tickets"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const visible = useMemo(() => {
    if (filter === "all") return tickets;
    if (filter === "open") return tickets.filter((t) => t.status !== "RESOLVED" && t.status !== "CLOSED");
    return tickets.filter((t) => t.status === filter);
  }, [tickets, filter]);

  const selected = tickets.find((t) => t.id === selectedId) ?? null;

  function replaceTicket(updated: AdminSupportTicket) {
    setTickets((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  }

  async function handleReply() {
    if (!selected || !reply.trim()) return;
    setBusy(true);
    setActionError("");
    try {
      replaceTicket(await replyAsSupport(selected.id, reply.trim()));
      setReply("");
    } catch (err) {
      setActionError(errorMessage(err, "Could not send reply"));
    } finally {
      setBusy(false);
    }
  }

  async function handleStatus() {
    if (!selected) return;
    setBusy(true);
    setActionError("");
    try {
      const extra =
        nextStatus === "ACTION_REQUIRED"
          ? { actionRequest: statusNote.trim() }
          : nextStatus === "RESOLVED" || nextStatus === "CLOSED"
            ? { resolution: statusNote.trim() }
            : {};
      replaceTicket(await setSupportTicketStatus(selected.id, nextStatus, extra));
      setStatusNote("");
    } catch (err) {
      setActionError(errorMessage(err, "Could not update status"));
    } finally {
      setBusy(false);
    }
  }

  const needsNote = nextStatus === "ACTION_REQUIRED" || nextStatus === "RESOLVED" || nextStatus === "CLOSED";

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
      <section className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
        <div className="flex items-center gap-2">
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value as Filter)}
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-xs font-semibold dark:border-slate-700 dark:bg-slate-900"
            aria-label="Filter tickets"
          >
            <option value="open">Open</option>
            {(Object.keys(STATUS_LABEL) as SupportTicketStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
            <option value="all">All</option>
          </select>
          <button
            type="button"
            onClick={load}
            className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-slate-300 px-3 py-2 text-xs font-semibold dark:border-slate-700"
          >
            <RefreshCw className="size-3.5" /> Refresh
          </button>
        </div>

        {loading ? (
          <p className="py-6 text-center text-xs text-slate-400">Loading tickets...</p>
        ) : error ? (
          <p className="py-6 text-center text-xs font-semibold text-red-600">{error}</p>
        ) : visible.length === 0 ? (
          <p className="py-6 text-center text-xs text-slate-400">No tickets here.</p>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {visible.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedId(t.id);
                    setActionError("");
                  }}
                  className={cn(
                    "w-full rounded-xl px-2 py-3 text-left hover:bg-slate-50 dark:hover:bg-slate-800",
                    selectedId === t.id && "bg-slate-50 dark:bg-slate-800",
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold text-slate-500">{t.ref}</span>
                    {t.timeSensitive && (
                      <span className="inline-flex items-center gap-1 text-[10px] font-bold text-red-600">
                        <AlertCircle className="size-3" /> Time-sensitive
                      </span>
                    )}
                    <span className={cn("ml-auto rounded-full border px-2 py-0.5 text-[10px] font-bold", STATUS_TONE[t.status])}>
                      {STATUS_LABEL[t.status]}
                    </span>
                  </div>
                  <p className="mt-1 text-sm font-semibold text-slate-900 dark:text-white">{t.issue}</p>
                  <p className="text-xs text-slate-500">
                    {t.userName || t.userEmail} · updated {new Date(t.updatedAt).toLocaleString()}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
        {!selected ? (
          <p className="py-10 text-center text-sm text-slate-400">Select a ticket to view it.</p>
        ) : (
          <div className="space-y-4">
            <div>
              <p className="text-xs font-bold text-slate-500">
                {selected.ref} · {selected.category} · reply via {selected.replyChannel === "both" ? "email + in-app" : "in-app"}
              </p>
              <h2 className="font-heading text-lg font-bold text-slate-900 dark:text-white">{selected.issue}</h2>
              <p className="text-xs text-slate-500">
                {selected.userName} &lt;{selected.userEmail}&gt;
                {selected.linkedLabel && <> · {selected.linkedLabel}</>}
              </p>
            </div>

            {selected.actionRequest && (
              <p className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                Waiting on user: {selected.actionRequest}
              </p>
            )}
            {selected.resolution && (
              <p className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-200">
                Outcome: {selected.resolution}
              </p>
            )}

            <div className="max-h-96 space-y-2 overflow-y-auto">
              {selected.messages.map((m) => (
                <div
                  key={m.id}
                  className={cn(
                    "max-w-[85%] rounded-2xl p-3 text-sm",
                    m.sender !== "USER"
                      ? "ml-auto bg-blue-50 text-blue-950 dark:bg-blue-950/40 dark:text-blue-100"
                      : "border border-slate-200 bg-slate-50 text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100",
                  )}
                >
                  <p className="text-[11px] font-bold text-slate-500">
                    {m.sender === "SUPPORT" ? "Support" : m.sender === "ASSISTANT" ? "Assistant (automatic)" : "User"} · {new Date(m.createdAt).toLocaleString()}
                  </p>
                  <p className="mt-1 whitespace-pre-wrap">{m.body}</p>
                  {m.attachments.map((a) => (
                    <a
                      key={a.id}
                      href={adminSupportAttachmentUrl(selected.id, a.id)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-xs font-semibold underline"
                    >
                      <FileText className="size-3.5" /> {a.originalFilename}
                    </a>
                  ))}
                </div>
              ))}
            </div>

            {selected.status !== "CLOSED" && (
              <>
                <div className="space-y-2 border-t border-slate-100 pt-4 dark:border-slate-800">
                  <label htmlFor="support-reply" className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Reply to user
                  </label>
                  <textarea
                    id="support-reply"
                    rows={3}
                    value={reply}
                    onChange={(e) => setReply(e.target.value)}
                    className={inputCls}
                  />
                  <button
                    type="button"
                    disabled={busy || !reply.trim()}
                    onClick={handleReply}
                    className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white disabled:opacity-50"
                  >
                    Send reply
                  </button>
                </div>

                <div className="space-y-2 border-t border-slate-100 pt-4 dark:border-slate-800">
                  <label htmlFor="support-status" className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Change status
                  </label>
                  <select
                    id="support-status"
                    value={nextStatus}
                    onChange={(e) => setNextStatus(e.target.value as typeof nextStatus)}
                    className={inputCls}
                  >
                    <option value="IN_REVIEW">In review</option>
                    <option value="ACTION_REQUIRED">Ask the user for something</option>
                    <option value="RESOLVED">Resolved (user can reopen)</option>
                    <option value="CLOSED">Closed (final)</option>
                  </select>
                  {needsNote && (
                    <textarea
                      rows={2}
                      value={statusNote}
                      onChange={(e) => setStatusNote(e.target.value)}
                      placeholder={nextStatus === "ACTION_REQUIRED" ? "What the user needs to send" : "Outcome shown to the user"}
                      className={inputCls}
                    />
                  )}
                  <button
                    type="button"
                    disabled={busy || (needsNote && !statusNote.trim() && !(nextStatus !== "ACTION_REQUIRED" && selected.resolution))}
                    onClick={handleStatus}
                    className="rounded-xl border border-slate-300 px-4 py-2 text-xs font-bold disabled:opacity-50 dark:border-slate-700"
                  >
                    Update status
                  </button>
                </div>
              </>
            )}

            {actionError && <p className="text-xs font-semibold text-red-600">{actionError}</p>}
          </div>
        )}
      </section>
    </div>
  );
}
