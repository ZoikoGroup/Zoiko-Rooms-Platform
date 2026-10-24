"use client";

import { useCallback, useEffect, useState } from "react";
import { Send } from "lucide-react";
import { RentalMessage } from "@/lib/types";
import { errorMessage } from "@/lib/user-api";
import { formatDateTime } from "@/lib/utils";

/** One in-app message thread (host <-> renter, or renter <-> subtenant).
 *  Loading the thread marks the other side's messages read; new replies show
 *  up without reloading the page. */
export function RentalChatThread({
  load,
  send,
  placeholder,
  emptyText,
}: {
  load: () => Promise<RentalMessage[]>;
  send: (body: string) => Promise<RentalMessage>;
  placeholder: string;
  emptyText: string;
}) {
  const [messages, setMessages] = useState<RentalMessage[] | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(() => {
    load()
      .then((items) => {
        setMessages(items);
        setError("");
      })
      .catch((err) => {
        setMessages((prev) => prev ?? []);
        setError(errorMessage(err, "Could not load messages."));
      });
  }, [load]);

  // Switching threads remounts this component (key), so no reset is needed here.
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  async function handleSend() {
    const text = input.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      const sent = await send(text);
      setMessages((prev) => [...(prev ?? []), sent]);
      setInput("");
      setError("");
    } catch (err) {
      setError(errorMessage(err, "Could not send your message."));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="max-h-[45vh] min-h-32 space-y-2 overflow-y-auto rounded-2xl border border-[#E1E7F1] bg-[#F7F9FD] p-3">
        {messages === null ? (
          <p className="text-xs text-[#5B6886]">Loading messages…</p>
        ) : messages.length === 0 ? (
          <p className="text-xs text-[#5B6886]">{emptyText}</p>
        ) : (
          messages.map((m) => (
            <div key={m.id} className={`flex ${m.mine ? "justify-end" : "justify-start"}`}>
              <div
                className={`max-w-[80%] rounded-2xl px-3 py-2 text-sm ${
                  m.mine ? "bg-[#0E2F73] text-white" : "border border-[#E1E7F1] bg-white text-[#0B1B3F]"
                }`}
              >
                <p className="whitespace-pre-wrap break-words">{m.body}</p>
                <time className={`mt-1 block text-[10px] ${m.mine ? "text-white/70" : "text-[#5B6886]"}`}>
                  {formatDateTime(m.createdAt)}
                </time>
              </div>
            </div>
          ))
        )}
      </div>
      {error && (
        <p className="text-xs text-[#D80B0B]" role="alert">
          {error}
        </p>
      )}
      <div className="flex items-center gap-2">
        <input
          type="text"
          value={input}
          maxLength={2000}
          placeholder={placeholder}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleSend();
          }}
          className="h-11 flex-1 rounded-xl border border-[#E1E7F1] bg-white px-3 text-sm outline-none focus:border-[#0E2F73]"
        />
        <button
          type="button"
          onClick={handleSend}
          disabled={sending || !input.trim()}
          aria-label="Send message"
          className="inline-flex h-11 w-11 items-center justify-center rounded-xl bg-[#0E2F73] text-white transition-colors hover:bg-[#0B2560] disabled:opacity-50"
        >
          <Send className="h-4 w-4" />
        </button>
      </div>
      <p className="text-xs text-[#5B6886]">Keep conversations in Zoiko Rooms — no need to share your phone or email.</p>
    </div>
  );
}
