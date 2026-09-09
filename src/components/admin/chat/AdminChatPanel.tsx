"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  Clock,
  History,
  Loader2,
  Mail,
  Mic,
  Send,
  Square,
  SquarePen,
  Trash2,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import {
  ChatConversation,
  ChatMessage,
  createChatConversation,
  deleteChatConversation,
  listChatMessages,
  listChatConversations,
  streamChatMessage,
} from "@/lib/chat";
import { listContactEmails, markContactEmailRead, getUnreadCount, ContactEmail } from "@/lib/contact-email";
import { MarkdownMessage } from "@/components/admin/chat/MarkdownMessage";
import { AssistantAvatar } from "@/components/chat/AssistantAvatar";
import { cn } from "@/lib/utils";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { useSpeechSynthesis } from "@/hooks/useSpeechSynthesis";
import { classifyRisk, type RiskClass } from "@/lib/risk";

interface AdminChatPanelProps {
  open: boolean;
  onClose: () => void;
  onUnread?: () => void;
}

const SUGGESTED_PROMPTS = [
  "Show my recent bookings",
  "Which listings aren't published yet?",
  "Summarize the revenue trend",
  "Which occupancies are due for rent?",
];

function relativeTime(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

function TypingDots() {
  return (
    <span className="flex items-center gap-1 py-1">
      <span className="typing-dot-1 h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-500" />
      <span className="typing-dot-2 h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-500" />
      <span className="typing-dot-3 h-1.5 w-1.5 rounded-full bg-slate-400 dark:bg-slate-500" />
    </span>
  );
}

export function AdminChatPanel({ open, onClose }: AdminChatPanelProps) {
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [activeId, setActiveId] = useState<number | null>(null);
  const [activeTitle, setActiveTitle] = useState<string>("");
  const [isContinuing, setIsContinuing] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streamingText, setStreamingText] = useState("");
  const [toolActivity, setToolActivity] = useState<string | null>(null);
  const [toolErrors, setToolErrors] = useState<string[]>([]);
  const [sending, setSending] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [lastFailedContent, setLastFailedContent] = useState<string | null>(null);

  const [pendingRisk, setPendingRisk] = useState<RiskClass>("R0");
  const [serverRisk, setServerRisk] = useState<RiskClass | null>(null);
  const [serverActionTier, setServerActionTier] = useState<string | null>(null);
  const [serverDetermination, setServerDetermination] = useState(false);

  const [historyOpen, setHistoryOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [connectedToast, setConnectedToast] = useState(false);

  const [emailsOpen, setEmailsOpen] = useState(false);
  const [emails, setEmails] = useState<ContactEmail[]>([]);
  const [emailsUnread, setEmailsUnread] = useState(0);
  const [emailsLoading, setEmailsLoading] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const streamBufRef = useRef("");
  const toastShownRef = useRef(false);

  const speech = useSpeechRecognition();
  const synthesis = useSpeechSynthesis();

  const scrollToBottom = useCallback((smooth = true) => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: smooth ? "smooth" : "auto",
    });
  }, []);

  // Load the conversation list for the history drawer. The panel itself always
  // opens on a deliberate new-chat state -- past conversations are reachable
  // via History, never auto-restored after a page refresh.
  useEffect(() => {
    if (!open) {
      abortRef.current?.abort();
      return;
    }
    let cancelled = false;
    stickToBottomRef.current = true;
    (async () => {
      try {
        const list = await listChatConversations();
        if (cancelled) return;
        setConversations(list);
      } catch {
        if (!cancelled) setError("Couldn't reach the assistant. Is the backend running?");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  useEffect(() => {
    if (stickToBottomRef.current) scrollToBottom();
  }, [messages, streamingText, toolActivity, scrollToBottom]);

  // Poll unread contact-email count while panel is open
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    async function poll() {
      try {
        const { count } = await getUnreadCount();
        if (!cancelled) setEmailsUnread(count);
      } catch { /* ignore */ }
    }
    poll();
    const id = setInterval(poll, 15000);
    return () => { cancelled = true; clearInterval(id); };
  }, [open]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottomRef.current = distanceFromBottom < 80;
  }

  function toggleMic() {
    if (speech.isListening) {
      speech.stopListening();
    } else {
      speech.clearError();
      speech.startListening((transcript) => {
        setInput(transcript);
      });
    }
  }

  async function openEmails() {
    setEmailsOpen(true);
    setEmailsLoading(true);
    try {
      const list = await listContactEmails();
      setEmails(list);
      setEmailsUnread(0);
    } catch { /* ignore */ }
    setEmailsLoading(false);
  }

  async function handleMarkEmailRead(id: number) {
    try {
      const updated = await markContactEmailRead(id);
      setEmails((prev) => prev.map((e) => (e.id === id ? updated : e)));
    } catch { /* ignore */ }
  }

  function startNewChat() {
    abortRef.current?.abort();
    synthesis.stop();
    speech.stopListening();
    setActiveId(null);
    setActiveTitle("");
    setIsContinuing(false);
    setMessages([]);
    setStreamingText("");
    setToolActivity(null);
    setToolErrors([]);
    setError(null);
    setLastFailedContent(null);
    setHistoryOpen(false);
  }

  async function selectConversation(id: number) {
    abortRef.current?.abort();
    synthesis.stop();
    speech.stopListening();
    try {
      const history = await listChatMessages(id);
      setActiveId(id);
      setActiveTitle(conversations.find((c) => c.id === id)?.title ?? "");
      setIsContinuing(true);
      setMessages(history);
      setStreamingText("");
      setToolActivity(null);
      setToolErrors([]);
      setError(null);
      setLastFailedContent(null);
      setHistoryOpen(false);
      stickToBottomRef.current = true;
    } catch {
      setError("Couldn't load that conversation.");
    }
  }

  async function handleDeleteConfirmed(id: number) {
    try {
      await deleteChatConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (id === activeId) startNewChat();
    } catch {
      setError("Couldn't delete that conversation.");
    } finally {
      setConfirmDeleteId(null);
    }
  }

  async function sendMessage(contentOverride?: string) {
    const content = (contentOverride ?? input).trim();
    if (!content || sending) return;

    speech.stopListening();
    abortRef.current?.abort();
    setInput("");
    setError(null);
    setLastFailedContent(null);
    setSending(true);
    setStreamingText("");
    setToolActivity(null);
    setToolErrors([]);
    setPendingRisk(classifyRisk(content));
    streamBufRef.current = "";

    setMessages((prev) => [
      ...prev,
      { id: Date.now(), role: "user", content, createdAt: new Date().toISOString() },
    ]);

    try {
      let conversationId = activeId;
      if (!conversationId) {
        const created = await createChatConversation();
        conversationId = created.id;
        setActiveId(conversationId);
        setActiveTitle(content.slice(0, 60));
        setConversations((prev) => [created, ...prev]);
      }

      const controller = new AbortController();
      abortRef.current = controller;

      let assistantDone = "";
      for await (const event of streamChatMessage(conversationId, content, controller.signal)) {
        if (event.type === "text") {
          streamBufRef.current += event.text;
          setStreamingText(streamBufRef.current);
        } else if (event.type === "tool") {
          setToolActivity(event.name);
        } else if (event.type === "tool_error") {
          setToolErrors((prev) => [...prev, event.name]);
        } else if (event.type === "done") {
          assistantDone = event.content;
          const g = event.guardrail;
          if (g) {
            // Trust the server-computed classification over any client guess.
            setServerRisk((g.risk as RiskClass) ?? null);
            setServerActionTier(g.action_tier ?? null);
            setServerDetermination(Boolean(g.determination_blocked));
            if (g.risk) setPendingRisk(g.risk as RiskClass);
          }
        } else if (event.type === "error") {
          throw new Error(event.message);
        }
      }

      const finalContent = assistantDone || streamBufRef.current;
      if (finalContent.trim()) {
        setMessages((prev) => [
          ...prev,
          {
            id: Date.now() + 1,
            role: "assistant",
            content: finalContent,
            createdAt: new Date().toISOString(),
          },
        ]);
      }
      if (
        process.env.NODE_ENV === "development" &&
        !toastShownRef.current &&
        assistantDone
      ) {
        toastShownRef.current = true;
        setConnectedToast(true);
        setTimeout(() => setConnectedToast(false), 3500);
      }
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        const partial = streamBufRef.current;
        if (partial.trim()) {
          setMessages((prev) => [
            ...prev,
            {
              id: Date.now() + 1,
              role: "assistant",
              content: `${partial}\n\n_(stopped)_`,
              createdAt: new Date().toISOString(),
            },
          ]);
        }
      } else {
        setError((err as Error).message);
        setLastFailedContent(content);
      }
    } finally {
      setStreamingText("");
      setToolActivity(null);
      setSending(false);
      abortRef.current = null;
    }
  }

  if (!open) return null;

  const waitingForFirstToken = sending && !streamingText && !toolActivity;

  return (
    <>
      {/* Click-away backdrop */}
      <button
        aria-label="Close chat"
        onClick={onClose}
        className="fixed inset-0 z-40 bg-black/20 backdrop-blur-[2px]"
      />

      <aside
        aria-label="Ask Zoiko admin AI assistant"
        className={cn(
          "animate-chat-panel-open fixed bottom-24 right-5 z-50 flex h-[85vh] w-[calc(100vw-2.5rem)] flex-col overflow-hidden rounded-2xl border border-slate-200/60 bg-white shadow-2xl shadow-primary-900/20 dark:border-white/10 dark:bg-slate-900",
          "sm:right-6 sm:max-w-[480px]"
        )}
      >
        <header className="border-b border-slate-100 dark:border-white/10">
          <div className="flex items-center justify-between px-4 py-3.5">
            <div className="flex min-w-0 items-center gap-3">
              <AssistantAvatar size="md" />
              <div className="min-w-0">
                <p className="truncate text-sm font-bold text-slate-800 dark:text-slate-100">
                  Ask Zoiko <span className="font-medium text-slate-400">· AI assistant</span>
                </p>
                <p className="truncate text-xs text-slate-400">
                  {isContinuing && activeTitle ? activeTitle : "Read-only · answers from your dashboard data"}
                </p>
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <button
                onClick={openEmails}
                aria-label="Contact emails from users"
                title="Contact Emails"
                className="relative flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200"
              >
                <Mail className="h-[18px] w-[18px]" />
                {emailsUnread > 0 && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent-600 px-1 text-[10px] font-bold text-white">
                    {emailsUnread > 99 ? "99+" : emailsUnread}
                  </span>
                )}
              </button>
              <button
                onClick={() => setHistoryOpen(true)}
                aria-label="Conversation history"
                title="History"
                className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200"
              >
                <History className="h-[18px] w-[18px]" />
              </button>
              <button
                onClick={startNewChat}
                aria-label="Start new chat"
                title="New chat"
                className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200"
              >
                <SquarePen className="h-[18px] w-[18px]" />
              </button>
              <button
                onClick={onClose}
                aria-label="Close chat"
                title="Close"
                className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200"
              >
                <X className="h-[18px] w-[18px]" />
              </button>
            </div>
          </div>
          <div className="border-t border-slate-100 bg-slate-50/70 px-4 py-1.5 text-[11px] leading-snug text-slate-500 dark:border-white/10 dark:bg-white/5 dark:text-slate-400">
            Provides information and helps you use Zoiko Rooms. It does not make platform decisions.
          </div>
        </header>

        {/* Context banner -- only shown when an older conversation is loaded */}
        {isContinuing && (
          <div className="flex items-center gap-1.5 border-b border-slate-100 bg-slate-50 px-5 py-1.5 text-[11px] font-medium text-slate-500 dark:border-white/10 dark:bg-white/5 dark:text-slate-400">
            <Clock className="h-3 w-3" />
            Continuing: {activeTitle || "previous conversation"}
          </div>
        )}

        <div ref={scrollRef} onScroll={handleScroll} className="flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {messages.length === 0 && !streamingText && (
            <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
              <AssistantAvatar size="lg" />
              <p className="animate-chat-fade-slide max-w-[300px] text-sm leading-relaxed text-slate-400" style={{ animationDelay: "0.08s" }}>
                Ask Zoiko is an AI assistant. It can look data up for you in your dashboard, but it does not make
                eligibility, compliance, ranking, application, payment, agreement, or tenancy decisions.
              </p>
              <div className="flex max-w-[320px] flex-wrap justify-center gap-2">
                {SUGGESTED_PROMPTS.map((prompt, i) => (
                  <button
                    key={prompt}
                    onClick={() => sendMessage(prompt)}
                    className="animate-chat-chip rounded-full px-3.5 py-2 text-xs font-medium text-slate-600 ring-1 ring-slate-200 transition-colors hover:bg-slate-100 hover:text-slate-800 dark:text-slate-300 dark:ring-white/10 dark:hover:bg-white/10"
                    style={{ animationDelay: `${0.15 + i * 0.06}s` }}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((message) =>
            message.role === "user" ? (
              <div key={message.id} className="flex justify-end">
                <div className="animate-chat-msg-right max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-primary-700 px-4 py-2.5 text-sm leading-relaxed text-white">
                  {message.content}
                </div>
              </div>
            ) : (
              <div key={message.id} className="animate-chat-msg-left flex flex-col">
                <div className="flex items-start gap-2.5">
                  <AssistantAvatar />
                  <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-white px-4 py-2.5 text-sm leading-relaxed text-slate-700 ring-1 ring-slate-200 dark:bg-white/5 dark:text-slate-200 dark:ring-white/10">
                    <MarkdownMessage content={message.content} />
                  </div>
                </div>
                {synthesis.isSupported && (
                  <div className="ml-9 mt-1 flex">
                    <button
                      onClick={() => synthesis.speak(String(message.id), message.content)}
                      aria-label={synthesis.speakingId === String(message.id) ? "Stop reading" : "Read aloud"}
                      title={synthesis.speakingId === String(message.id) ? "Stop reading" : "Read aloud"}
                      className={cn(
                        "flex h-6 w-6 items-center justify-center rounded-full transition-colors",
                        synthesis.speakingId === String(message.id)
                          ? "bg-primary-100 text-primary-700 dark:bg-primary-500/20 dark:text-primary-300"
                          : "text-slate-300 hover:bg-slate-100 hover:text-slate-500 dark:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-300"
                      )}
                    >
                      {synthesis.speakingId === String(message.id) ? (
                        <VolumeX className="h-3.5 w-3.5" />
                      ) : (
                        <Volume2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                )}
              </div>
            )
          )}

          {(streamingText || waitingForFirstToken || toolActivity || toolErrors.length > 0) && (
            <div className="animate-chat-msg-left flex flex-col gap-2">
              {(serverRisk === "R3" || serverRisk === "R4" || serverRisk === "R2" || pendingRisk === "R2") && (
                <div className="max-w-[85%] rounded-xl border border-amber-200 bg-amber-50 px-3.5 py-2.5 text-xs leading-relaxed text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">
                  For confirmed status on this topic, use the record shown in Zoiko Rooms or contact the
                  appropriate support route. Ask Zoiko can explain status but does not determine or change it.
                </div>
              )}
              {(serverActionTier === "A3" || serverDetermination) && (
                <div className="max-w-[85%] rounded-xl border border-red-200 bg-red-50 px-3.5 py-2.5 text-xs leading-relaxed text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
                  This topic requires human confirmation. A Zoiko Rooms team member should confirm the
                  authoritative status before you act on it.
                </div>
              )}
              <div className="flex items-start gap-2.5">
                <AssistantAvatar />
              <div className="max-w-[85%] rounded-2xl rounded-tl-sm bg-white px-4 py-2.5 text-sm leading-relaxed text-slate-700 ring-1 ring-slate-200 dark:bg-white/5 dark:text-slate-200 dark:ring-white/10">
                {streamingText && <MarkdownMessage content={streamingText} />}
                {waitingForFirstToken && <TypingDots />}
                {toolActivity && (
                  <span className="mt-1 flex items-center gap-1.5 text-xs text-slate-400">
                    <Loader2 className="h-3 w-3 animate-spin" /> Looking up {toolActivity}…
                  </span>
                )}
                {toolErrors.map((name, i) => (
                  <p key={`${name}-${i}`} className="mt-1 flex items-start gap-1.5 text-xs text-amber-600 dark:text-amber-400">
                    <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
                    Couldn&apos;t fetch that data ({name}). Please try rephrasing or try again.
                  </p>
                ))}
              </div>
            </div>
          </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-2xl bg-accent-50 px-4 py-3 text-sm text-accent-700 dark:bg-accent-500/10 dark:text-accent-300">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span className="flex-1">{error}</span>
              {lastFailedContent && !sending && (
                <button
                  onClick={() => sendMessage(lastFailedContent)}
                  className="shrink-0 rounded-full px-3 py-1 text-xs font-semibold underline-offset-2 hover:underline"
                >
                  Retry
                </button>
              )}
            </div>
          )}
          <div className="h-px" />
        </div>

        <footer className="border-t border-slate-100 p-4 dark:border-white/10">
          {speech.error && (
            <div className="mb-2 flex items-center gap-2 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-500/10 dark:text-amber-300">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span className="flex-1">{speech.error}</span>
              <button onClick={speech.clearError} className="shrink-0 text-amber-500 hover:text-amber-700">
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
          <form
            onSubmit={(e) => {
              e.preventDefault();
              sendMessage();
            }}
            className="flex items-end gap-2"
          >
            {speech.isSupported && (
              <button
                type="button"
                onClick={toggleMic}
                disabled={sending}
                aria-label={speech.isListening ? "Stop listening" : "Start voice input"}
                title={speech.isListening ? "Stop listening" : "Voice input"}
                className={cn(
                  "flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl transition-colors disabled:opacity-50",
                  speech.isListening
                    ? "bg-accent-600 text-white animate-pulse"
                    : "bg-slate-100 text-slate-400 hover:bg-slate-200 hover:text-slate-600 dark:bg-white/10 dark:text-slate-400 dark:hover:bg-white/15 dark:hover:text-slate-200"
                )}
              >
                <Mic className="h-5 w-5" />
              </button>
            )}
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  sendMessage();
                }
              }}
              rows={1}
              placeholder={speech.isListening ? "Listening…" : "Ask about your operations…"}
              disabled={sending}
              className="max-h-32 flex-1 resize-none rounded-2xl bg-slate-100 px-4 py-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 disabled:opacity-60 dark:bg-white/10 dark:text-slate-200"
            />
            {sending ? (
              <button
                type="button"
                onClick={() => abortRef.current?.abort()}
                aria-label="Stop generating"
                title="Stop generating"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-slate-300 text-slate-700 transition-colors hover:bg-slate-400 dark:bg-white/20 dark:text-slate-100"
              >
                <Square className="h-4 w-4 fill-current" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!input.trim()}
                aria-label="Send message"
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-accent-600 text-white transition-colors hover:bg-accent-700 disabled:opacity-50"
              >
                <Send className="h-5 w-5" />
              </button>
            )}
          </form>
          <p className="mt-3 text-center text-[10px] leading-relaxed text-slate-300 dark:text-slate-600">
            Zoiko Rooms is a trading name of Zoiko Realty Group.
          </p>
        </footer>

        {/* Contact emails overlay */}
        {emailsOpen && (
          <div className="animate-fade-up absolute inset-0 z-20 flex flex-col rounded-2xl bg-white dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-white/10">
              <p className="text-sm font-bold text-slate-800 dark:text-slate-100">Contact Emails</p>
              <button
                onClick={() => setEmailsOpen(false)}
                aria-label="Close emails"
                className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200"
              >
                <X className="h-[18px] w-[18px]" />
              </button>
            </div>
            <div className="flex-1 space-y-1 overflow-y-auto p-3">
              {emailsLoading && (
                <p className="px-3 py-8 text-center text-sm text-slate-400">Loading...</p>
              )}
              {!emailsLoading && emails.length === 0 && (
                <p className="px-3 py-8 text-center text-sm text-slate-400">No messages from users yet.</p>
              )}
              {!emailsLoading && emails.map((email) => (
                <div
                  key={email.id}
                  className={cn(
                    "rounded-xl border px-3.5 py-3 transition-colors",
                    email.isRead
                      ? "border-slate-100 bg-white dark:border-white/5 dark:bg-white/[0.02]"
                      : "border-primary-200 bg-primary-50/50 dark:border-primary-500/20 dark:bg-primary-500/5"
                  )}
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">
                        {email.subject}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-400">
                        {email.userName || email.userEmail} &middot; {relativeTime(email.createdAt)}
                      </p>
                    </div>
                    {!email.isRead && (
                      <button
                        onClick={() => handleMarkEmailRead(email.id)}
                        className="shrink-0 rounded-lg px-2 py-1 text-[11px] font-medium text-primary-600 hover:bg-primary-100 dark:text-primary-400 dark:hover:bg-primary-500/10"
                      >
                        Mark read
                      </button>
                    )}
                  </div>
                  <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed text-slate-600 dark:text-slate-300">
                    {email.message}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Conversation history slide-over */}
        {historyOpen && (
          <div className="animate-fade-up absolute inset-0 z-20 flex flex-col rounded-2xl bg-white dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-white/10">
              <p className="text-sm font-bold text-slate-800 dark:text-slate-100">Conversations</p>
              <button
                onClick={() => setHistoryOpen(false)}
                aria-label="Close history"
                className="flex h-9 w-9 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200"
              >
                <X className="h-[18px] w-[18px]" />
              </button>
            </div>
            <div className="flex-1 space-y-1 overflow-y-auto p-3">
              {conversations.length === 0 && (
                <p className="px-3 py-8 text-center text-sm text-slate-400">No past conversations yet.</p>
              )}
              {conversations.map((conversation) =>
                confirmDeleteId === conversation.id ? (
                  <div
                    key={conversation.id}
                    className="flex items-center justify-between gap-2 rounded-xl bg-accent-50 px-3 py-2.5 text-xs dark:bg-accent-500/10"
                  >
                    <span className="font-medium text-accent-700 dark:text-accent-300">Delete this chat?</span>
                    <span className="flex gap-1.5">
                      <button
                        onClick={() => handleDeleteConfirmed(conversation.id)}
                        className="rounded-lg bg-accent-600 px-2.5 py-1 font-semibold text-white hover:bg-accent-700"
                      >
                        Delete
                      </button>
                      <button
                        onClick={() => setConfirmDeleteId(null)}
                        className="rounded-lg px-2.5 py-1 font-medium text-slate-500 hover:bg-black/5 dark:hover:bg-white/10"
                      >
                        Cancel
                      </button>
                    </span>
                  </div>
                ) : (
                  <div
                    key={conversation.id}
                    className={cn(
                      "group flex items-center gap-2 rounded-xl px-3 py-2.5 transition-colors",
                      conversation.id === activeId
                        ? "bg-primary-50 dark:bg-primary-500/10"
                        : "hover:bg-slate-100 dark:hover:bg-white/10"
                    )}
                  >
                    <button onClick={() => selectConversation(conversation.id)} className="min-w-0 flex-1 text-left">
                      <p className="truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                        {conversation.title}
                      </p>
                      <p className="text-xs text-slate-400">{relativeTime(conversation.updatedAt)}</p>
                    </button>
                    <button
                      onClick={() => setConfirmDeleteId(conversation.id)}
                      aria-label={`Delete ${conversation.title}`}
                      className="shrink-0 rounded-lg p-1.5 text-slate-300 transition-colors hover:bg-accent-50 hover:text-accent-600 group-hover:visible dark:text-slate-600 dark:hover:bg-accent-500/10"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                )
              )}
            </div>
          </div>
        )}
      </aside>

      {/* Dev-only confirmation that the Groq key is live */}
      {connectedToast && process.env.NODE_ENV === "development" && (
        <div className="animate-fade-up fixed bottom-6 left-6 z-[70] flex items-center gap-2.5 rounded-2xl bg-white px-4 py-3 shadow-xl shadow-primary-900/20 ring-1 ring-emerald-200 dark:bg-slate-800 dark:ring-emerald-500/30">
          <span className="flex h-6 w-6 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400">
            <svg viewBox="0 0 20 20" fill="currentColor" className="h-3.5 w-3.5">
              <path
                fillRule="evenodd"
                d="M16.7 5.3a1 1 0 010 1.4l-7.5 7.5a1 1 0 01-1.4 0l-3.5-3.5a1 1 0 111.4-1.4l2.8 2.79 6.8-6.8a1 1 0 011.4 0z"
                clipRule="evenodd"
              />
            </svg>
          </span>
          <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">
            Ask Zoiko connected (Zoiko Assist)
          </p>
        </div>
      )}
    </>
  );
}
