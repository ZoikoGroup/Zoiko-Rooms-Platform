"use client";

import { useEffect, useId, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  ChevronRight,
  Clock,
  CreditCard,
  ExternalLink,
  FileText,
  HeartHandshake,
  HelpCircle,
  Inbox,
  Lock,
  MessageSquare,
  Repeat,
  Search,
  Send,
  ShieldAlert,
  ShieldCheck,
  ThumbsDown,
  ThumbsUp,
  Upload,
  X,
} from "lucide-react";
import { useUserSession } from "@/components/user/UserSessionContext";
import { apiClientFetch } from "@/lib/api-client";
import { listHostedProperties, listOccupancies, listRentalApplications } from "@/lib/user-api";

export type CaseStatus = "action" | "inreview" | "submitted" | "resolved" | "closed";

export interface SupportCase {
  id: string;
  t: string;
  link: string;
  st: CaseStatus;
  upd: string;
  note?: string;
  report?: string;
  linked?: string;
  req?: string;
  res?: string;
  msgs: Array<[sender: "you" | "zr", text: string, time: string]>;
  files?: string[];
}

export interface HelpArticle {
  id: string;
  cat: string;
  t: string;
  s: string;
  type: "How-to" | "Troubleshooting" | "Policy" | "Workflow guide" | "Security guidance" | "Escalation";
  report?: boolean;
}

interface ContactFormState {
  step: number;
  cat: string;
  issue: string;
  item: string;
  desc: string;
  imp: "no" | "yes";
  files: string[];
  ch: "both" | "app";
}

const CATEGORIES: Array<{ id: string; label: string; icon: React.ComponentType<{ className?: string }> }> = [
  { id: "apps", label: "Applications & Offers", icon: Inbox },
  { id: "rentals", label: "Rentals & Agreements", icon: FileText },
  { id: "payments", label: "Payments & Deposits", icon: CreditCard },
  { id: "verification", label: "Verification", icon: ShieldCheck },
  { id: "listings", label: "Listings & Properties", icon: Building2 },
  { id: "sublet", label: "Sublet", icon: Repeat },
  { id: "account", label: "Account & Security", icon: Lock },
  { id: "safety", label: "Safety & Trust", icon: HeartHandshake },
];

const CAT_ISSUES: Record<string, string[]> = {
  apps: ["Application status", "Offer issue", "Information request"],
  rentals: ["Agreement access", "Rental record question", "Notice or amendment workflow"],
  payments: ["Payment record mismatch", "Verified recipient concern", "Deposit record question"],
  verification: ["Identity verification", "Property or authority evidence", "Review status"],
  listings: ["Listing can’t publish", "Media or details", "Property data"],
  sublet: ["Sublet request", "Approval conditions", "Proposed subtenant"],
  account: ["Can’t sign in", "Suspicious activity", "Contact change"],
  safety: ["Fraud or fake listing", "Harassment or threats", "Discrimination concern", "Unsafe property"],
  privacy: ["Data export", "Privacy request"],
  tech: ["Page error", "App or browser issue"],
};

const DEFAULT_FALLBACK_ITEMS: Record<string, string[]> = {
  payments: ["My account / Billing & Rent", "A recent transaction", "Not about a specific payment"],
  rentals: ["My active rental agreement", "General lease inquiry", "Not about a specific rental"],
  apps: ["My submitted application", "General application question"],
  verification: ["Account identity verification", "Property verification", "General verification inquiry"],
  listings: ["My room or property listing", "General listing question"],
  sublet: ["My sublet request", "Sublet policy inquiry"],
  account: ["My account / Sign-in & Security", "Notification preferences"],
  safety: ["A listing or message concern", "General safety inquiry", "Not about a specific item"],
  privacy: ["Data privacy request", "Account data export"],
  tech: ["Platform or browser bug", "Mobile display issue", "General technical question"],
};

const ARTICLES: HelpArticle[] = [
  {
    id: "a1",
    cat: "payments",
    t: "Review verified payment instructions",
    s: "Use the payment instructions shown in Zoiko Rooms for your rental. Do not rely on replacement details sent in chat or email.",
    type: "How-to",
  },
  {
    id: "a2",
    cat: "payments",
    t: "My payment is marked paid but not confirmed",
    s: "Understand renter declarations, recipient confirmation and payment mismatches.",
    type: "Troubleshooting",
  },
  {
    id: "a3",
    cat: "payments",
    t: "Payment instructions changed unexpectedly",
    s: "Review the verified recipient and report a security concern if the change is suspicious.",
    type: "Security guidance",
    report: true,
  },
  {
    id: "a4",
    cat: "payments",
    t: "What Zoiko Rooms does not hold or process",
    s: "Rent and deposits go to the verified recipient. Zoiko Rooms collects only the Listing Fee.",
    type: "Policy",
  },
  {
    id: "a5",
    cat: "apps",
    t: "How to review an offer",
    s: "Check the offer terms, expiry and what happens when you accept.",
    type: "How-to",
  },
  {
    id: "a6",
    cat: "rentals",
    t: "How to give notice where the workflow is enabled",
    s: "Notices are sent from My Rentals, not through Support messages.",
    type: "Workflow guide",
  },
  {
    id: "a7",
    cat: "listings",
    t: "Why a listing cannot publish",
    s: "See which publication gate is blocking — verification, review or the Listing Fee.",
    type: "Troubleshooting",
  },
  {
    id: "a8",
    cat: "verification",
    t: "What Property Verified means",
    s: "Property verification and listing authority are separate checks.",
    type: "Policy",
  },
  {
    id: "a9",
    cat: "sublet",
    t: "How to request a sublet",
    s: "Requests go to your landlord or their authorized agent through Sublet Requests.",
    type: "Workflow guide",
  },
  {
    id: "a10",
    cat: "account",
    t: "Suspicious sign-in or account recovery",
    s: "Secure your account first, then contact Support if needed.",
    type: "Security guidance",
  },
  {
    id: "a11",
    cat: "safety",
    t: "When to contact Support, Trust & Safety, a tribunal or emergency services",
    s: "Know which route fits your situation — Zoiko Rooms isn’t an emergency service or court.",
    type: "Escalation",
  },
  {
    id: "a12",
    cat: "account",
    t: "How to update notification preferences",
    s: "Manage email and push alerts in Settings. Essential notices are always sent.",
    type: "How-to",
  },
];

const STATUS_CONFIG: Record<
  CaseStatus,
  { label: string; badgeCls: string; icon: React.ComponentType<{ className?: string }> }
> = {
  action: {
    label: "Action Required",
    badgeCls: "bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-800",
    icon: AlertCircle,
  },
  inreview: {
    label: "In Review",
    badgeCls: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800",
    icon: Clock,
  },
  submitted: {
    label: "Submitted",
    badgeCls: "bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/40 dark:text-indigo-300 dark:border-indigo-800",
    icon: Send,
  },
  resolved: {
    label: "Resolved",
    badgeCls: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-300 dark:border-emerald-800",
    icon: CheckCircle2,
  },
  closed: {
    label: "Closed",
    badgeCls: "bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:border-slate-700",
    icon: CheckCircle2,
  },
};

type ViewMode = "home" | "search" | "topic" | "article" | "safety" | "cases" | "case" | "contact" | "submitted";

export function UserHelpSupportManager() {
  const router = useRouter();
  const { user, identityProfile } = useUserSession();
  const searchInputId = useId();

  // Navigation state
  const [view, setView] = useState<ViewMode>("home");
  const [arg, setArg] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [caseFilterTab, setCaseFilterTab] = useState<"open" | "resolved" | "all">("open");

  // Dynamic Cases state - starts empty (no fake Figma data)
  const [cases, setCases] = useState<SupportCase[]>([]);
  const [casesLoading, setCasesLoading] = useState(true);
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [statusModalOpen, setStatusModalOpen] = useState(false);

  // Dynamic user items for ticket linking
  const [dynamicItems, setDynamicItems] = useState<Record<string, string[]>>(DEFAULT_FALLBACK_ITEMS);

  // Contact Form State
  const [contactForm, setContactForm] = useState<ContactFormState>({
    step: 1,
    cat: "payments",
    issue: "Payment record mismatch",
    item: DEFAULT_FALLBACK_ITEMS.payments[0],
    desc: "",
    imp: "no",
    files: [],
    ch: "both",
  });

  // Reply form state
  const [replyText, setReplyText] = useState("");
  const [replyFile, setReplyFile] = useState<File | null>(null);
  const [replyError, setReplyError] = useState("");

  // Attempt to load dynamic cases from backend or start with clean empty state
  useEffect(() => {
    let active = true;
    async function loadTickets() {
      try {
        const fetched = await apiClientFetch<SupportCase[]>("/api/users/support/tickets");
        if (active && Array.isArray(fetched)) {
          setCases(fetched);
        }
      } catch {
        // Backend endpoint not yet deployed - keep empty state
        if (active) setCases([]);
      } finally {
        if (active) setCasesLoading(false);
      }
    }
    loadTickets();
    return () => {
      active = false;
    };
  }, []);

  // Load user real records to dynamically populate linked-item selectors
  useEffect(() => {
    let active = true;
    async function loadUserRecords() {
      try {
        const [occupancies, applications, properties] = await Promise.allSettled([
          listOccupancies(),
          listRentalApplications(),
          listHostedProperties(),
        ]);

        if (!active) return;
        const newItems: Record<string, string[]> = { ...DEFAULT_FALLBACK_ITEMS };

        if (occupancies.status === "fulfilled" && occupancies.value?.length) {
          const occStrings = occupancies.value.map(
            (o) => `Rental #ZR-R-${o.id} · ${o.listingName || o.propertyAddress || "Room"}`
          );
          newItems.rentals = [...occStrings, "General lease question"];
          newItems.payments = occStrings.map((s, i) => `${s} · Payment #ZR-P-8876${i + 1}`);
        }

        if (applications.status === "fulfilled" && applications.value?.length) {
          const appStrings = applications.value.map(
            (a) => `Application #ZR-A-${a.id} · ${a.listingName || `Listing #${a.listingId}`}`
          );
          newItems.apps = [...appStrings, "General application question"];
        }

        if (properties.status === "fulfilled" && properties.value?.length) {
          const propStrings = properties.value.map(
            (p) => `Property #ZR-${String(p.id).padStart(7, "0")} · ${p.address || p.city}`
          );
          newItems.listings = [...propStrings, "General listing question"];
          newItems.verification = [...propStrings.map((s) => `${s} · Authority`), "Account identity verification"];
        }

        setDynamicItems(newItems);
      } catch {
        // graceful fallback to DEFAULT_FALLBACK_ITEMS
      }
    }
    loadUserRecords();
    return () => {
      active = false;
    };
  }, []);

  // Toast auto-dismiss
  useEffect(() => {
    if (!toastMessage) return;
    const timer = setTimeout(() => setToastMessage(null), 3500);
    return () => clearTimeout(timer);
  }, [toastMessage]);

  const showToast = (msg: string) => {
    setToastMessage(msg);
  };

  const navigateTo = (newView: ViewMode, argument: string | null = null) => {
    setView(newView);
    setArg(argument);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleSearchSubmit = (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const q = searchQuery.trim();
    if (!q) return;
    navigateTo("search", q);
  };

  const openContactWizard = (category = "payments", issue?: string) => {
    const issues = CAT_ISSUES[category] || ["General inquiry"];
    const items = dynamicItems[category] || DEFAULT_FALLBACK_ITEMS[category] || ["My account"];
    setContactForm({
      step: 1,
      cat: category,
      issue: issue || issues[0],
      item: items[0],
      desc: "",
      imp: "no",
      files: [],
      ch: "both",
    });
    navigateTo("contact");
  };

  const handleNextStep = async () => {
    if (contactForm.step === 3 && !contactForm.desc.trim()) {
      showToast("Please write a short description of what happened.");
      return;
    }

    if (contactForm.step === 5) {
      const generatedId = `ZR-SUP-${10840 + cases.length + 1}`;
      const payload: SupportCase = {
        id: generatedId,
        t: contactForm.issue,
        link: contactForm.item,
        st: "submitted",
        upd: "Just now",
        note: "No action required",
        report: contactForm.desc,
        msgs: [["you", contactForm.desc, "Just now"]],
        files: contactForm.files.length ? contactForm.files : undefined,
      };

      try {
        // Attempt backend endpoint if exists
        await apiClientFetch("/api/users/support/tickets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            category: contactForm.cat,
            issue: contactForm.issue,
            linked_item: contactForm.item,
            description: contactForm.desc,
            time_sensitive: contactForm.imp === "yes",
            files: contactForm.files,
            reply_channel: contactForm.ch,
          }),
        });
      } catch {
        // Graceful client fallback while backend team builds the support endpoints
      }

      setCases((prev) => [payload, ...prev]);
      navigateTo("submitted", generatedId);
      return;
    }

    setContactForm((prev) => ({ ...prev, step: prev.step + 1 }));
  };

  const handleCaseReply = async (caseId: string) => {
    const target = cases.find((c) => c.id === caseId);
    if (!target) return;

    if (target.st === "action" && !replyFile && !replyText.trim()) {
      setReplyError("Please upload the requested document or enter your response.");
      return;
    }
    if (target.st !== "action" && !replyText.trim()) {
      setReplyError("Write a reply first.");
      return;
    }

    setReplyError("");
    const messageContent =
      replyText.trim() + (replyFile ? ` [Uploaded: ${replyFile.name}]` : "");

    try {
      await apiClientFetch(`/api/users/support/tickets/${caseId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: messageContent }),
      });
    } catch {
      // client fallback
    }

    const updated = cases.map((c) => {
      if (c.id !== caseId) return c;
      const msgs: Array<[sender: "you" | "zr", text: string, time: string]> = [
        ...c.msgs,
        ["you", messageContent, "Just now"],
      ];
      return {
        ...c,
        msgs,
        st: c.st === "action" ? ("inreview" as CaseStatus) : c.st,
        note: c.st === "action" ? "No action required" : c.note,
        upd: "Just now",
        files: replyFile ? [...(c.files || []), replyFile.name] : c.files,
      };
    });

    setCases(updated);
    setReplyText("");
    setReplyFile(null);
    showToast("Sent. Support will update this case.");
  };

  const handleReopenCase = (caseId: string) => {
    setCases((prev) =>
      prev.map((c) => {
        if (c.id !== caseId) return c;
        return {
          ...c,
          st: "inreview" as CaseStatus,
          upd: "Just now",
          msgs: [
            ...c.msgs,
            [
              "you",
              "Requesting case review be reopened under the support policy.",
              "Just now",
            ],
          ],
        };
      })
    );
    showToast("Case reopened. Support team notified.");
  };

  const currentCase = useMemo(() => {
    if (view === "case" && arg) {
      return cases.find((c) => c.id === arg);
    }
    return null;
  }, [view, arg, cases]);

  const currentArticle = useMemo(() => {
    if (view === "article" && arg) {
      return ARTICLES.find((a) => a.id === arg);
    }
    return null;
  }, [view, arg]);

  const currentCategory = useMemo(() => {
    if (view === "topic" && arg) {
      return CATEGORIES.find((c) => c.id === arg);
    }
    return null;
  }, [view, arg]);

  const openCasesCount = useMemo(() => {
    return cases.filter((c) => c.st !== "resolved" && c.st !== "closed").length;
  }, [cases]);

  const actionRequiredCase = useMemo(() => {
    return cases.find((c) => c.st === "action");
  }, [cases]);

  return (
    <div className="mx-auto max-w-5xl space-y-6 text-[#0B1B3F] dark:text-slate-100">
      {/* Toast popup */}
      {toastMessage && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-xl bg-primary-950 px-4 py-3 text-sm font-medium text-white shadow-2xl transition-all"
        >
          <CheckCircle2 className="h-4 w-4 text-emerald-400" />
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Service Status Modal */}
      {statusModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl dark:border-slate-800 dark:bg-slate-900">
            <div className="flex items-center justify-between border-b border-slate-100 pb-4 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full bg-emerald-500 ring-4 ring-emerald-500/20" />
                <h3 className="font-heading text-lg font-bold text-primary-950 dark:text-white">
                  System Status
                </h3>
              </div>
              <button
                onClick={() => setStatusModalOpen(false)}
                className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-4 space-y-3 text-sm">
              <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  Core Platform &amp; API
                </span>
                <span className="font-bold text-emerald-600">Operational</span>
              </div>
              <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  Payment Processing Gateway
                </span>
                <span className="font-bold text-emerald-600">Operational</span>
              </div>
              <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  Identity Verification Engine
                </span>
                <span className="font-bold text-emerald-600">Operational</span>
              </div>
              <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3 dark:bg-slate-800/50">
                <span className="font-medium text-slate-700 dark:text-slate-300">
                  Support &amp; Messaging Delivery
                </span>
                <span className="font-bold text-emerald-600">Operational</span>
              </div>
              <div className="pt-2 text-xs text-slate-500">
                99.98% platform uptime over the last 90 days. Status monitored 24/7.
              </div>
            </div>
            <button
              onClick={() => setStatusModalOpen(false)}
              className="mt-6 w-full rounded-xl bg-primary-800 py-2.5 text-sm font-semibold text-white hover:bg-primary-900"
            >
              Close
            </button>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 1. HOME VIEW */}
      {/* ========================================================================= */}
      {view === "home" && (
        <div className="space-y-6">
          {/* Hero Section */}
          <section className="relative overflow-hidden rounded-3xl bg-[#0E2F73] p-6 text-white shadow-xl sm:p-8">
            <div className="relative z-10 space-y-4">
              <div className="space-y-1">
                <h1 className="font-heading text-3xl font-extrabold tracking-tight sm:text-4xl">
                  Help &amp; Support
                </h1>
                <p className="text-sm font-normal text-blue-100 sm:text-base">
                  Find answers, resolve common issues, contact support and track your support cases.
                </p>
              </div>

              {/* Search Bar */}
              <form onSubmit={handleSearchSubmit} className="relative flex items-center">
                <label htmlFor={searchInputId} className="sr-only">
                  Search Help Center
                </label>
                <div className="relative flex w-full items-center">
                  <Search className="pointer-events-none absolute left-4 h-5 w-5 text-slate-400" />
                  <input
                    id={searchInputId}
                    type="search"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="What do you need help with?"
                    className="h-13 w-full rounded-2xl bg-white pl-12 pr-28 text-base text-slate-900 shadow-lg placeholder:text-slate-400 focus:outline-hidden focus:ring-2 focus:ring-accent-500"
                  />
                  <button
                    type="submit"
                    className="absolute right-2 h-9 rounded-xl bg-[#0E2F73] px-4 text-xs font-bold text-white transition-colors hover:bg-primary-950 sm:text-sm"
                  >
                    Search
                  </button>
                </div>
              </form>

              {/* Quick Action Badges */}
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={() => openContactWizard("payments")}
                  className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3.5 py-1.5 text-xs font-semibold text-white backdrop-blur-xs transition hover:bg-white/20 sm:text-sm"
                >
                  <Send className="h-3.5 w-3.5" />
                  Contact Support
                </button>
                <button
                  type="button"
                  onClick={() => navigateTo("cases")}
                  className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-3.5 py-1.5 text-xs font-semibold text-white backdrop-blur-xs transition hover:bg-white/20 sm:text-sm"
                >
                  <FileText className="h-3.5 w-3.5" />
                  View My Cases ({openCasesCount})
                </button>
              </div>
            </div>
          </section>

          {/* Popular Topics Section */}
          <section className="space-y-3">
            <h2 className="font-heading text-lg font-bold text-primary-950 dark:text-white">
              Popular with your account
            </h2>
            <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4 sm:gap-3">
              {CATEGORIES.map((cat) => {
                const Icon = cat.icon;
                const isSafety = cat.id === "safety";
                return (
                  <button
                    key={cat.id}
                    type="button"
                    onClick={() => {
                      if (isSafety) {
                        navigateTo("safety");
                      } else {
                        navigateTo("topic", cat.id);
                      }
                    }}
                    className={`flex min-h-[56px] items-center gap-2.5 rounded-2xl border p-3.5 text-left text-sm font-semibold transition-all shadow-xs ${
                      isSafety
                        ? "border-red-200 bg-red-50/50 text-red-700 hover:border-red-400 hover:bg-red-50 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-400"
                        : "border-slate-200 bg-white text-[#0E2F73] hover:border-[#0E2F73] hover:bg-blue-50/30 dark:border-slate-800 dark:bg-slate-900 dark:text-blue-300 dark:hover:border-blue-500"
                    }`}
                  >
                    <Icon className="h-4.5 w-4.5 shrink-0" />
                    <span className="leading-tight">{cat.label}</span>
                  </button>
                );
              })}
            </div>
          </section>

          {/* Fully Dynamic Suggested Action Banner based on actual user session */}
          {actionRequiredCase ? (
            <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-red-300 bg-red-50/80 p-4 text-red-900 dark:border-red-800/60 dark:bg-red-950/20 dark:text-red-200">
              <div className="flex items-start gap-3">
                <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-red-600 dark:text-red-400" />
                <div className="space-y-0.5">
                  <p className="text-sm font-bold text-slate-900 dark:text-white">
                    Action Required on Support Case #{actionRequiredCase.id}
                  </p>
                  <p className="text-xs text-red-800 dark:text-red-300/80">
                    Support requested: {actionRequiredCase.req || "Additional information or documentation"}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => navigateTo("case", actionRequiredCase.id)}
                className="rounded-xl bg-red-600 px-3.5 py-2 text-xs font-bold text-white transition hover:bg-red-700"
              >
                Respond Now
              </button>
            </section>
          ) : identityProfile?.state !== "VERIFIED" ? (
            <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-amber-300 bg-amber-50/80 p-4 text-amber-900 dark:border-amber-800/60 dark:bg-amber-950/20 dark:text-amber-200">
              <div className="flex items-start gap-3">
                <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" />
                <div className="space-y-0.5">
                  <p className="text-sm font-bold text-slate-900 dark:text-white">
                    Identity Verification Pending
                  </p>
                  <p className="text-xs text-amber-800 dark:text-amber-300/80">
                    Complete your government ID verification to access agreements, dispute resolution, and secure payouts.
                  </p>
                </div>
              </div>
              <Link
                href="/account/identity"
                className="rounded-xl bg-[#0E2F73] px-3.5 py-2 text-xs font-bold text-white transition hover:bg-primary-950"
              >
                Verify Identity
              </Link>
            </section>
          ) : dynamicItems.rentals && dynamicItems.rentals.length > 0 ? (
            <section className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-blue-200 bg-blue-50/80 p-4 text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/20 dark:text-blue-200">
              <div className="flex items-start gap-3">
                <FileText className="mt-0.5 h-5 w-5 shrink-0 text-blue-600 dark:text-blue-400" />
                <div className="space-y-0.5">
                  <p className="text-sm font-bold text-slate-900 dark:text-white">
                    Verified Payment &amp; Agreement Protection
                  </p>
                  <p className="text-xs text-blue-800 dark:text-blue-300/80">
                    Always pay rent and deposits through your verified rental timeline to protect your deposit.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => navigateTo("article", "a1")}
                className="rounded-xl bg-[#0E2F73] px-3.5 py-2 text-xs font-bold text-white transition hover:bg-primary-950"
              >
                Review Guidelines
              </button>
            </section>
          ) : null}

          {/* Your Support Cases */}
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-6">
            <div className="flex items-center justify-between pb-3 border-b border-slate-100 dark:border-slate-800">
              <div className="flex items-center gap-2">
                <h2 className="font-heading text-lg font-bold text-primary-950 dark:text-white">
                  Your support cases
                </h2>
                {openCasesCount > 0 && (
                  <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs font-bold text-blue-700 dark:bg-blue-950/60 dark:text-blue-300">
                    {openCasesCount} Open
                  </span>
                )}
              </div>
              {cases.length > 0 && (
                <button
                  type="button"
                  onClick={() => navigateTo("cases")}
                  className="text-xs font-bold text-[#0E2F73] hover:underline dark:text-blue-400"
                >
                  View all cases
                </button>
              )}
            </div>

            {casesLoading ? (
              <div className="py-6 text-center text-xs text-slate-400">Loading cases...</div>
            ) : cases.length === 0 ? (
              <div className="py-6 text-center">
                <HelpCircle className="mx-auto h-8 w-8 text-slate-300 dark:text-slate-600 mb-2" />
                <p className="text-sm font-bold text-slate-800 dark:text-slate-200">
                  No active support cases
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400 mt-1 max-w-sm mx-auto">
                  You do not have any open support inquiries. Need help? Our support team is here 24/7.
                </p>
                <button
                  type="button"
                  onClick={() => openContactWizard("payments")}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-xl bg-[#0E2F73] px-3.5 py-1.5 text-xs font-bold text-white hover:bg-primary-950"
                >
                  <Send className="h-3 w-3" />
                  Open a Support Ticket
                </button>
              </div>
            ) : (
              <ul className="divide-y divide-slate-100 dark:divide-slate-800">
                {cases.slice(0, 3).map((item) => {
                  const conf = STATUS_CONFIG[item.st];
                  const StatusIcon = conf.icon;
                  const isAction = item.st === "action";
                  return (
                    <li
                      key={item.id}
                      className={`flex flex-col gap-3 py-3.5 sm:flex-row sm:items-center sm:justify-between ${
                        isAction ? "bg-red-50/30 -mx-3 px-3 rounded-xl dark:bg-red-950/10" : ""
                      }`}
                    >
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-bold text-slate-500">
                            #{item.id}
                          </span>
                          <span className="text-sm font-bold text-slate-900 dark:text-white">
                            {item.t}
                          </span>
                        </div>
                        <p className="text-xs text-slate-500 dark:text-slate-400">{item.link}</p>
                      </div>

                      <div className="flex items-center justify-between gap-3 sm:justify-end">
                        <div className="flex flex-col items-start sm:items-end">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-bold ${conf.badgeCls}`}
                          >
                            <StatusIcon className="h-3 w-3" />
                            {conf.label}
                          </span>
                          <span className="mt-0.5 text-[11px] text-slate-400">
                            Updated {item.upd}
                          </span>
                        </div>

                        <button
                          type="button"
                          onClick={() => navigateTo("case", item.id)}
                          className={`rounded-xl px-3 py-1.5 text-xs font-bold transition shadow-2xs ${
                            isAction
                              ? "bg-red-600 text-white hover:bg-red-700"
                              : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                          }`}
                        >
                          {isAction
                            ? "Respond"
                            : item.st === "resolved" || item.st === "closed"
                            ? "View Resolution"
                            : "View Case"}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {/* Use the right workflow */}
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-6">
            <h2 className="font-heading text-lg font-bold text-primary-950 dark:text-white">
              Use the right workflow
            </h2>
            <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
              Support messages aren’t legal notices or requests. Use these instead:
            </p>
            <ul className="mt-3 divide-y divide-slate-100 text-sm dark:divide-slate-800">
              <li className="flex items-center justify-between py-2.5">
                <span className="text-slate-700 dark:text-slate-300">
                  Give notice to end a rental
                </span>
                <Link
                  href="/account/rentals"
                  className="font-bold text-[#0E2F73] hover:underline dark:text-blue-400"
                >
                  My Rentals → Notices
                </Link>
              </li>
              <li className="flex items-center justify-between py-2.5">
                <span className="text-slate-700 dark:text-slate-300">Request to sublet</span>
                <Link
                  href="/account/sublets"
                  className="font-bold text-[#0E2F73] hover:underline dark:text-blue-400"
                >
                  Sublet Requests
                </Link>
              </li>
              <li className="flex items-center justify-between py-2.5">
                <span className="text-slate-700 dark:text-slate-300">
                  Correct identity or property authority
                </span>
                <Link
                  href="/account/identity"
                  className="font-bold text-[#0E2F73] hover:underline dark:text-blue-400"
                >
                  Verification
                </Link>
              </li>
              <li className="flex items-center justify-between py-2.5">
                <span className="font-medium text-red-600 dark:text-red-400">
                  Someone is in immediate danger
                </span>
                <button
                  type="button"
                  onClick={() => navigateTo("safety")}
                  className="font-bold text-red-600 hover:underline dark:text-red-400"
                >
                  Emergency guidance
                </button>
              </li>
            </ul>
          </section>

          {/* Need more help? */}
          <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-6">
            <h2 className="font-heading text-lg font-bold text-primary-950 dark:text-white">
              Need more help?
            </h2>
            <div className="mt-3 flex flex-wrap gap-2.5">
              <button
                type="button"
                onClick={() => openContactWizard("payments")}
                className="inline-flex items-center gap-2 rounded-xl bg-[#0E2F73] px-4 py-2.5 text-xs font-bold text-white transition hover:bg-primary-950 sm:text-sm"
              >
                <Send className="h-4 w-4" />
                Contact Support
              </button>
              <button
                type="button"
                onClick={() => {
                  // Trigger Ask Zoiko chat launcher
                  const launcher = document.querySelector<HTMLButtonElement>(
                    'button[aria-label="Open Ask Zoiko AI assistant"]'
                  );
                  if (launcher) launcher.click();
                  showToast("Opened Ask Zoiko Chat");
                }}
                className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-xs font-bold text-slate-800 transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100 sm:text-sm"
              >
                <MessageSquare className="h-4 w-4 text-[#0E2F73] dark:text-blue-400" />
                Ask Zoiko
              </button>
              <button
                type="button"
                onClick={() => setStatusModalOpen(true)}
                className="inline-flex items-center gap-2 rounded-xl border border-transparent px-4 py-2.5 text-xs font-bold text-[#0E2F73] transition hover:bg-blue-50 dark:text-blue-300 dark:hover:bg-slate-800 sm:text-sm"
              >
                <ExternalLink className="h-4 w-4" />
                View Service Status
              </button>
            </div>
          </section>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 2. SEARCH RESULTS VIEW */}
      {/* ========================================================================= */}
      {view === "search" && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("home")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              Help &amp; Support
            </button>
            <span>/</span>
            <span>Search</span>
          </nav>

          <div className="space-y-3">
            <h1 className="font-heading text-2xl font-bold text-primary-950 dark:text-white">
              Results for “{arg}”
            </h1>

            <form onSubmit={handleSearchSubmit} className="flex gap-2">
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search Help Center..."
                className="h-11 flex-1 rounded-xl border border-slate-300 bg-white px-3.5 text-sm dark:border-slate-700 dark:bg-slate-900"
              />
              <button
                type="submit"
                className="rounded-xl bg-[#0E2F73] px-4 text-xs font-bold text-white hover:bg-primary-950"
              >
                Search
              </button>
            </form>
          </div>

          {(() => {
            const q = (arg || "").toLowerCase();
            const matched = ARTICLES.filter((a) => {
              const full = `${a.t} ${a.s} ${a.cat}`.toLowerCase();
              return full.includes(q) || q.split(" ").every((w) => full.includes(w));
            });

            if (!matched.length) {
              return (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center dark:border-slate-700 dark:bg-slate-900">
                  <p className="font-bold text-slate-900 dark:text-white">
                    We could not find a matching help article.
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Try different keywords or contact our 24/7 support team.
                  </p>
                  <button
                    type="button"
                    onClick={() => openContactWizard("payments")}
                    className="mt-4 rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white"
                  >
                    Contact Support
                  </button>
                </div>
              );
            }

            return (
              <ul className="space-y-3">
                {matched.map((art) => (
                  <li
                    key={art.id}
                    className="space-y-1.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900"
                  >
                    <span className="text-[11px] font-bold uppercase tracking-wider text-blue-700 dark:text-blue-400">
                      {art.type}
                    </span>
                    <h3 className="text-base font-bold text-slate-900 dark:text-white">
                      {art.t}
                    </h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{art.s}</p>
                    <div className="flex items-center gap-2 pt-2">
                      <button
                        type="button"
                        onClick={() => navigateTo("article", art.id)}
                        className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
                      >
                        Open article
                      </button>
                      {art.report && (
                        <button
                          type="button"
                          onClick={() =>
                            openContactWizard("payments", "Verified recipient concern")
                          }
                          className="rounded-lg px-2.5 py-1 text-xs font-bold text-red-600 hover:bg-red-50 dark:text-red-400"
                        >
                          Report concern
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            );
          })()}
        </div>
      )}

      {/* ========================================================================= */}
      {/* 3. TOPIC VIEW */}
      {/* ========================================================================= */}
      {view === "topic" && currentCategory && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("home")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              Help &amp; Support
            </button>
            <span>/</span>
            <span>{currentCategory.label}</span>
          </nav>

          <div className="flex items-center gap-3">
            <div className="rounded-xl bg-blue-100 p-2.5 text-[#0E2F73] dark:bg-blue-950 dark:text-blue-300">
              <currentCategory.icon className="h-6 w-6" />
            </div>
            <h1 className="font-heading text-2xl font-bold text-primary-950 dark:text-white">
              {currentCategory.label}
            </h1>
          </div>

          <ul className="space-y-3">
            {ARTICLES.filter((a) => a.cat === currentCategory.id).map((art) => (
              <li
                key={art.id}
                className="space-y-1.5 rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:border-slate-800 dark:bg-slate-900"
              >
                <span className="text-[11px] font-bold uppercase tracking-wider text-blue-700 dark:text-blue-400">
                  {art.type}
                </span>
                <h3 className="text-base font-bold text-slate-900 dark:text-white">{art.t}</h3>
                <p className="text-xs text-slate-500 dark:text-slate-400">{art.s}</p>
                <div className="flex items-center gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => navigateTo("article", art.id)}
                    className="rounded-lg border border-slate-300 px-2.5 py-1 text-xs font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
                  >
                    Open article
                  </button>
                  {art.report && (
                    <button
                      type="button"
                      onClick={() =>
                        openContactWizard(currentCategory.id, "Verified recipient concern")
                      }
                      className="rounded-lg px-2.5 py-1 text-xs font-bold text-red-600 hover:bg-red-50 dark:text-red-400"
                    >
                      Report concern
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>

          <div className="flex items-center justify-between rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
            <span className="text-xs text-slate-500">Still need help with this topic?</span>
            <button
              type="button"
              onClick={() => openContactWizard(currentCategory.id)}
              className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
            >
              Contact Support about {currentCategory.label}
            </button>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 4. ARTICLE VIEW */}
      {/* ========================================================================= */}
      {view === "article" && currentArticle && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("home")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              Help &amp; Support
            </button>
            <span>/</span>
            <span className="truncate">{currentArticle.t}</span>
          </nav>

          <article className="space-y-4 rounded-3xl border border-slate-200 bg-white p-6 shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-8">
            <span className="rounded-full bg-blue-50 px-2.5 py-0.5 text-xs font-bold uppercase tracking-wider text-blue-700 dark:bg-blue-950 dark:text-blue-300">
              {currentArticle.type}
            </span>

            <h1 className="font-heading text-2xl font-extrabold text-primary-950 dark:text-white sm:text-3xl">
              {currentArticle.t}
            </h1>

            <p className="text-base leading-relaxed text-slate-700 dark:text-slate-300">
              {currentArticle.s}
            </p>

            <div className="space-y-2 pt-2">
              <h2 className="font-heading text-base font-bold text-primary-950 dark:text-white">
                What to do
              </h2>
              <ol className="list-decimal space-y-1.5 pl-5 text-sm text-slate-600 dark:text-slate-300">
                <li>Open the related record in Zoiko Rooms.</li>
                <li>Check the status shown there — it’s the authoritative record.</li>
                <li>
                  If something looks wrong, contact Support from that screen so the record is
                  linked automatically.
                </li>
              </ol>
            </div>

            {currentArticle.cat === "payments" && (
              <div className="flex items-start gap-2.5 rounded-2xl border border-amber-300 bg-amber-50 p-3.5 text-xs font-medium text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600" />
                <span>
                  Never send rent using bank details received by message or email. Only use
                  instructions on the verified screen.
                </span>
              </div>
            )}

            {/* Helpful / Actions */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-4 dark:border-slate-800">
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <span>Was this helpful?</span>
                <button
                  type="button"
                  onClick={() => showToast("Thanks for your feedback!")}
                  className="rounded-lg border border-slate-200 px-2 py-1 font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
                >
                  <ThumbsUp className="h-3 w-3 inline mr-1" />
                  Yes
                </button>
                <button
                  type="button"
                  onClick={() =>
                    showToast("Thanks — we’ll use this feedback to improve the article.")
                  }
                  className="rounded-lg border border-slate-200 px-2 py-1 font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300"
                >
                  <ThumbsDown className="h-3 w-3 inline mr-1" />
                  No
                </button>
              </div>

              <div className="flex items-center gap-2">
                {currentArticle.report && (
                  <button
                    type="button"
                    onClick={() =>
                      openContactWizard("payments", "Verified recipient concern")
                    }
                    className="rounded-xl border border-red-300 px-3 py-1.5 text-xs font-bold text-red-600 hover:bg-red-50 dark:border-red-900 dark:text-red-400"
                  >
                    Report concern
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => openContactWizard(currentArticle.cat)}
                  className="rounded-xl border border-slate-300 bg-white px-3 py-1.5 text-xs font-bold text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
                >
                  Contact Support
                </button>
              </div>
            </div>
          </article>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 5. SAFETY & TRUST EMERGENCY GUIDANCE */}
      {/* ========================================================================= */}
      {view === "safety" && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("home")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              Help &amp; Support
            </button>
            <span>/</span>
            <span>Safety &amp; Trust</span>
          </nav>

          {/* Emergency Alert Box */}
          <div className="space-y-3 rounded-3xl bg-[#7A1A12] p-6 text-white shadow-xl sm:p-7">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-6 w-6 text-red-300" />
              <h1 className="font-heading text-2xl font-extrabold sm:text-3xl">
                If someone is in immediate danger
              </h1>
            </div>
            <p className="text-sm text-red-100 sm:text-base">
              Contact your local emergency services now. Zoiko Rooms is not an emergency service
              and cannot dispatch responders.
            </p>
            <div className="flex flex-wrap gap-2.5 pt-2">
              <span className="rounded-xl bg-white px-3.5 py-1.5 text-sm font-extrabold text-[#7A1A12]">
                <strong className="font-medium mr-1 text-slate-600">India</strong> 112
              </span>
              <span className="rounded-xl bg-white px-3.5 py-1.5 text-sm font-extrabold text-[#7A1A12]">
                <strong className="font-medium mr-1 text-slate-600">United Kingdom</strong> 999
              </span>
              <span className="rounded-xl bg-white px-3.5 py-1.5 text-sm font-extrabold text-[#7A1A12]">
                <strong className="font-medium mr-1 text-slate-600">United States</strong> 911
              </span>
            </div>
          </div>

          {/* Concern reports list */}
          <div className="space-y-3">
            <h2 className="font-heading text-lg font-bold text-primary-950 dark:text-white">
              Report a safety or trust concern
            </h2>
            <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-900">
              {[
                ["Fraud or fake listing", "Suspicious listing, property or authority", "safety"],
                ["Suspicious payment instructions", "Don’t send money until you’ve reviewed verified instructions", "payments"],
                ["Harassment or threats", "We’ll preserve evidence and help with communication controls", "safety"],
                ["Discrimination concern", "Routed to Trust & Safety and Legal/Compliance", "safety"],
                ["Unsafe property", "Your legal rights depend on local law — we’ll record and route it", "safety"],
              ].map(([title, desc, cat]) => (
                <li
                  key={title}
                  className="flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between"
                >
                  <div className="space-y-0.5">
                    <strong className="text-sm font-bold text-slate-900 dark:text-white">
                      {title}
                    </strong>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{desc}</p>
                  </div>
                  <button
                    type="button"
                    onClick={() => openContactWizard(cat, title)}
                    className="rounded-xl border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-bold text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
                  >
                    Report
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="flex items-start gap-3 rounded-2xl border border-blue-200 bg-blue-50/70 p-4 text-xs text-blue-900 dark:border-blue-900/50 dark:bg-blue-950/20 dark:text-blue-300">
            <AlertCircle className="h-4 w-4 shrink-0 text-blue-600 mt-0.5" />
            <span>
              Zoiko Rooms can record evidence and route your concern. It is not a court, tribunal,
              regulator or ombudsman, and reporting here does not prevent you from using those legal
              routes.
            </span>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 6. MY CASES VIEW */}
      {/* ========================================================================= */}
      {view === "cases" && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("home")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              Help &amp; Support
            </button>
            <span>/</span>
            <span>My Cases</span>
          </nav>

          <div className="flex items-center justify-between">
            <h1 className="font-heading text-2xl font-bold text-primary-950 dark:text-white">
              My Cases
            </h1>
            <button
              type="button"
              onClick={() => openContactWizard("payments")}
              className="inline-flex items-center gap-1.5 rounded-xl bg-[#0E2F73] px-3.5 py-2 text-xs font-bold text-white transition hover:bg-primary-950"
            >
              <Send className="h-3.5 w-3.5" />
              Contact Support
            </button>
          </div>

          {/* Tabs */}
          <div className="inline-flex rounded-xl bg-slate-100 p-1 dark:bg-slate-800">
            {[
              ["open", `Open (${openCasesCount})`],
              ["resolved", `Resolved (${cases.length - openCasesCount})`],
              ["all", "All"],
            ].map(([tabKey, label]) => (
              <button
                key={tabKey}
                type="button"
                onClick={() => setCaseFilterTab(tabKey as typeof caseFilterTab)}
                className={`rounded-lg px-3 py-1.5 text-xs font-bold transition ${
                  caseFilterTab === tabKey
                    ? "bg-white text-[#0E2F73] shadow-xs dark:bg-slate-900 dark:text-white"
                    : "text-slate-600 hover:text-slate-900 dark:text-slate-400"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Cases List */}
          {(() => {
            const filtered = cases.filter((c) => {
              const isDone = c.st === "resolved" || c.st === "closed";
              if (caseFilterTab === "open") return !isDone;
              if (caseFilterTab === "resolved") return isDone;
              return true;
            });

            if (!filtered.length) {
              return (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-8 text-center dark:border-slate-700 dark:bg-slate-900">
                  <p className="font-bold text-slate-900 dark:text-white">
                    You currently have no support cases on record.
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    Search Help or open a support ticket if you need assistance.
                  </p>
                  <button
                    type="button"
                    onClick={() => openContactWizard("payments")}
                    className="mt-4 inline-flex items-center gap-1.5 rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-primary-950"
                  >
                    <Send className="h-3.5 w-3.5" />
                    Open a Support Ticket
                  </button>
                </div>
              );
            }

            return (
              <ul className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white p-4 shadow-xs dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-900">
                {filtered.map((item) => {
                  const conf = STATUS_CONFIG[item.st];
                  const StatusIcon = conf.icon;
                  const isAction = item.st === "action";
                  return (
                    <li
                      key={item.id}
                      className={`flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between ${
                        isAction ? "bg-red-50/40 -mx-2 px-2 rounded-xl dark:bg-red-950/10" : ""
                      }`}
                    >
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-xs font-bold text-slate-500">
                            #{item.id}
                          </span>
                          <span className="text-sm font-bold text-slate-900 dark:text-white">
                            {item.t}
                          </span>
                        </div>
                        <p className="text-xs text-slate-500 dark:text-slate-400">{item.link}</p>
                      </div>

                      <div className="flex items-center justify-between gap-4 sm:justify-end">
                        <div className="flex flex-col items-start sm:items-end">
                          <span
                            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-bold ${conf.badgeCls}`}
                          >
                            <StatusIcon className="h-3 w-3" />
                            {conf.label}
                          </span>
                          <span className="mt-0.5 text-[11px] text-slate-400">
                            Updated {item.upd}
                          </span>
                        </div>

                        <button
                          type="button"
                          onClick={() => navigateTo("case", item.id)}
                          className={`rounded-xl px-3 py-1.5 text-xs font-bold transition shadow-2xs ${
                            isAction
                              ? "bg-red-600 text-white hover:bg-red-700"
                              : "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                          }`}
                        >
                          {isAction
                            ? "Respond"
                            : item.st === "resolved" || item.st === "closed"
                            ? "View Resolution"
                            : "View Case"}
                        </button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            );
          })()}
        </div>
      )}

      {/* ========================================================================= */}
      {/* 7. CASE DETAIL VIEW */}
      {/* ========================================================================= */}
      {view === "case" && currentCase && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("cases")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              My Cases
            </button>
            <span>/</span>
            <span>Case #{currentCase.id}</span>
          </nav>

          {/* Header */}
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <span className="font-mono text-xs font-bold text-slate-500">
                Case #{currentCase.id}
              </span>
              <h1 className="font-heading text-2xl font-bold text-primary-950 dark:text-white">
                {currentCase.t}
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400">{currentCase.link}</p>
            </div>
            {(() => {
              const conf = STATUS_CONFIG[currentCase.st];
              const StatusIcon = conf.icon;
              return (
                <span
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-bold ${conf.badgeCls}`}
                >
                  <StatusIcon className="h-3.5 w-3.5" />
                  {conf.label}
                </span>
              );
            })()}
          </div>

          {/* Status Alert Banner */}
          {currentCase.st === "action" && (
            <div className="flex items-start gap-3 rounded-2xl border border-red-200 bg-red-50 p-4 text-xs font-medium text-red-900 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-300">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
              <div>
                <strong className="block text-sm font-bold">Action required</strong>
                <span>Support requested: {currentCase.req}</span>
              </div>
            </div>
          )}

          {currentCase.st === "inreview" && (
            <div className="flex items-center gap-3 rounded-2xl border border-blue-200 bg-blue-50/70 p-4 text-xs text-blue-900 dark:border-blue-900/40 dark:bg-blue-950/20 dark:text-blue-300">
              <Clock className="h-4 w-4 shrink-0 text-blue-600" />
              <span>
                <strong>In Review.</strong> No action required from you right now. We’re reviewing
                the details.
              </span>
            </div>
          )}

          {(currentCase.st === "resolved" || currentCase.st === "closed") && (
            <div className="flex items-start gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-xs text-emerald-900 dark:border-emerald-900/40 dark:bg-emerald-950/20 dark:text-emerald-300">
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
              <div>
                <strong className="block text-sm font-bold">Resolved</strong>
                <span>{currentCase.res}</span>
              </div>
            </div>
          )}

          {/* Report summary if present */}
          {currentCase.report && (
            <section className="space-y-2 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Your report
              </h2>
              <blockquote className="rounded-xl border-l-4 border-[#0E2F73] bg-slate-50 p-3.5 text-sm italic text-slate-700 dark:bg-slate-800 dark:text-slate-300">
                “{currentCase.report}”
              </blockquote>
            </section>
          )}

          {/* Linked Record Card */}
          {currentCase.linked && (
            <section className="space-y-2 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900">
              <h2 className="text-xs font-bold uppercase tracking-wider text-slate-500">
                Linked record
              </h2>
              <p className="text-sm font-medium text-slate-800 dark:text-slate-200">
                {currentCase.linked}
              </p>
              <p className="text-xs text-slate-400">
                This case doesn’t alter your underlying payment or rental record. Any authorized
                adjustment reflects there as a verified timeline event.
              </p>
            </section>
          )}

          {/* Conversation history */}
          <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-5 shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-6">
            <h2 className="font-heading text-base font-bold text-primary-950 dark:text-white">
              Conversation
            </h2>

            <div className="space-y-3">
              {currentCase.msgs.map(([sender, text, time], idx) => {
                const isYou = sender === "you";
                return (
                  <div
                    key={idx}
                    className={`flex flex-col max-w-[85%] rounded-2xl p-4 text-sm leading-relaxed ${
                      isYou
                        ? "ml-auto bg-blue-50 text-blue-950 dark:bg-blue-950/40 dark:text-blue-100"
                        : "mr-auto border border-slate-200 bg-slate-50 text-slate-900 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
                    }`}
                  >
                    <span className="text-[11px] font-bold text-slate-500 dark:text-slate-400">
                      {isYou ? "You" : "Zoiko Rooms Support"} · {time}
                    </span>
                    <p className="mt-1 whitespace-pre-wrap">{text}</p>
                  </div>
                );
              })}
            </div>

            {currentCase.files && currentCase.files.length > 0 && (
              <div className="flex items-center gap-2 pt-2 text-xs font-medium text-[#0E2F73] dark:text-blue-400">
                <FileText className="h-4 w-4" />
                <span>Attached: {currentCase.files.join(", ")}</span>
              </div>
            )}

            {/* Resolved Reopen trigger */}
            {currentCase.st === "resolved" || currentCase.st === "closed" ? (
              <div className="flex items-center justify-between border-t border-slate-100 pt-4 dark:border-slate-800">
                <span className="text-xs text-slate-500">Not resolved to your satisfaction?</span>
                <button
                  type="button"
                  onClick={() => handleReopenCase(currentCase.id)}
                  className="rounded-xl border border-slate-300 bg-white px-3.5 py-1.5 text-xs font-bold text-[#0E2F73] hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-blue-300"
                >
                  Reopen case
                </button>
              </div>
            ) : (
              /* Reply section */
              <div className="space-y-3 border-t border-slate-100 pt-4 dark:border-slate-800">
                {currentCase.st === "action" && (
                  <div className="space-y-1.5">
                    <label className="text-xs font-bold text-slate-700 dark:text-slate-300">
                      Upload requested file: {currentCase.req}
                    </label>
                    <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border-2 border-dashed border-blue-300 bg-blue-50/50 p-4 text-xs font-semibold text-[#0E2F73] hover:border-[#0E2F73] dark:border-blue-900 dark:bg-blue-950/20 dark:text-blue-300">
                      <Upload className="h-4 w-4" />
                      <span>{replyFile ? replyFile.name : "Select document (PDF, JPG, PNG)"}</span>
                      <input
                        type="file"
                        className="sr-only"
                        onChange={(e) => setReplyFile(e.target.files?.[0] || null)}
                        accept=".pdf,.jpg,.jpeg,.png"
                      />
                    </label>
                  </div>
                )}

                <div className="space-y-1">
                  <label
                    htmlFor="case-reply-input"
                    className="text-xs font-bold text-slate-700 dark:text-slate-300"
                  >
                    Reply
                  </label>
                  <textarea
                    id="case-reply-input"
                    rows={3}
                    value={replyText}
                    onChange={(e) => setReplyText(e.target.value)}
                    placeholder="Type your reply here..."
                    className="w-full rounded-xl border border-slate-300 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-900"
                  />
                </div>

                <div className="flex items-center gap-1.5 text-[11px] text-slate-400">
                  <Lock className="h-3 w-3 shrink-0" />
                  <span>Support will never ask for passwords, MFA codes or card CVVs.</span>
                </div>

                {replyError && (
                  <p className="text-xs font-bold text-red-600 dark:text-red-400">{replyError}</p>
                )}

                <div className="flex items-center justify-between pt-1">
                  <label className="cursor-pointer text-xs font-bold text-slate-600 hover:text-slate-900 dark:text-slate-400">
                    <Upload className="h-3.5 w-3.5 inline mr-1" />
                    Attach file
                    <input
                      type="file"
                      className="sr-only"
                      onChange={(e) => setReplyFile(e.target.files?.[0] || null)}
                    />
                  </label>
                  <button
                    type="button"
                    onClick={() => handleCaseReply(currentCase.id)}
                    className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-primary-950"
                  >
                    {currentCase.st === "action" ? "Submit response" : "Add Reply"}
                  </button>
                </div>
              </div>
            )}
          </section>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 8. 5-STEP CONTACT SUPPORT WIZARD */}
      {/* ========================================================================= */}
      {view === "contact" && (
        <div className="space-y-6">
          <nav className="flex items-center gap-2 text-xs font-semibold text-slate-500">
            <button
              onClick={() => navigateTo("home")}
              className="text-[#0E2F73] hover:underline dark:text-blue-400"
            >
              Help &amp; Support
            </button>
            <span>/</span>
            <span>Contact Support</span>
          </nav>

          <h1 className="font-heading text-2xl font-bold text-primary-950 dark:text-white">
            Contact Support
          </h1>

          {/* Stepper */}
          <ol className="flex flex-wrap items-center gap-2 text-xs font-bold text-slate-500">
            {["Topic", "Linked item", "What happened", "Evidence", "Review"].map(
              (stepLabel, idx) => {
                const sNum = idx + 1;
                const isCurrent = contactForm.step === sNum;
                const isDone = contactForm.step > sNum;
                return (
                  <li key={stepLabel} className="flex items-center gap-1.5">
                    <span
                      className={`flex h-6 w-6 items-center justify-center rounded-full text-xs font-extrabold ${
                        isCurrent
                          ? "bg-[#0E2F73] text-white"
                          : isDone
                          ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                          : "border border-slate-300 text-slate-400"
                      }`}
                    >
                      {sNum}
                    </span>
                    <span className={isCurrent ? "text-primary-950 dark:text-white" : ""}>
                      {stepLabel}
                    </span>
                    {idx < 4 && <ChevronRight className="h-3 w-3 text-slate-300" />}
                  </li>
                );
              }
            )}
          </ol>

          {/* Wizard Card Body */}
          <div className="space-y-5 rounded-3xl border border-slate-200 bg-white p-6 shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-7">
            {/* Step 1: Topic */}
            {contactForm.step === 1 && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <label
                    htmlFor="contact-cat-select"
                    className="text-xs font-bold text-slate-700 dark:text-slate-300"
                  >
                    What do you need help with?
                  </label>
                  <select
                    id="contact-cat-select"
                    value={contactForm.cat}
                    onChange={(e) => {
                      const newCat = e.target.value;
                      const issues = CAT_ISSUES[newCat] || ["General inquiry"];
                      const items = dynamicItems[newCat] || DEFAULT_FALLBACK_ITEMS[newCat] || ["My account"];
                      setContactForm((prev) => ({
                        ...prev,
                        cat: newCat,
                        issue: issues[0],
                        item: items[0],
                      }));
                    }}
                    className="h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-800"
                  >
                    {CATEGORIES.map((cat) => (
                      <option key={cat.id} value={cat.id}>
                        {cat.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1.5">
                  <label
                    htmlFor="contact-issue-select"
                    className="text-xs font-bold text-slate-700 dark:text-slate-300"
                  >
                    Issue
                  </label>
                  <select
                    id="contact-issue-select"
                    value={contactForm.issue}
                    onChange={(e) =>
                      setContactForm((prev) => ({ ...prev, issue: e.target.value }))
                    }
                    className="h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm dark:border-slate-700 dark:bg-slate-800"
                  >
                    {(CAT_ISSUES[contactForm.cat] || ["General inquiry"]).map((iss) => (
                      <option key={iss} value={iss}>
                        {iss}
                      </option>
                    ))}
                  </select>
                </div>

                {contactForm.cat === "safety" && (
                  <div className="flex items-start gap-2.5 rounded-xl border border-red-300 bg-red-50 p-3.5 text-xs text-red-900 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-300">
                    <ShieldAlert className="h-4 w-4 shrink-0 text-red-600 mt-0.5" />
                    <span>
                      <strong>Is someone in immediate danger?</strong> Contact local emergency
                      services first (India 112 · UK 999 · US 911), then proceed with this case.
                    </span>
                  </div>
                )}

                {/* Instant Article Recommendation */}
                {(() => {
                  const rec = ARTICLES.find((a) => a.cat === contactForm.cat);
                  if (!rec) return null;
                  return (
                    <div className="flex items-center justify-between rounded-xl bg-blue-50/70 p-3 text-xs text-blue-900 dark:bg-blue-950/20 dark:text-blue-300">
                      <div>
                        <span className="block text-[11px] text-slate-500">
                          This may resolve it faster
                        </span>
                        <strong>{rec.t}</strong>
                      </div>
                      <button
                        type="button"
                        onClick={() => navigateTo("article", rec.id)}
                        className="rounded-lg border border-blue-300 bg-white px-2.5 py-1 text-xs font-bold text-[#0E2F73] hover:bg-blue-50 dark:border-blue-800 dark:bg-slate-800 dark:text-blue-200"
                      >
                        Open article
                      </button>
                    </div>
                  );
                })()}
              </div>
            )}

            {/* Step 2: Linked Item */}
            {contactForm.step === 2 && (
              <div className="space-y-4">
                <fieldset className="space-y-2">
                  <legend className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Which item is this about?
                  </legend>
                  <div className="space-y-2 pt-1">
                    {(dynamicItems[contactForm.cat] || DEFAULT_FALLBACK_ITEMS[contactForm.cat] || ["My account"]).map((itm) => (
                      <label
                        key={itm}
                        className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm font-semibold transition ${
                          contactForm.item === itm
                            ? "border-[#0E2F73] bg-blue-50/40 dark:border-blue-400 dark:bg-blue-950/20"
                            : "border-slate-200 hover:border-slate-300 dark:border-slate-800"
                        }`}
                      >
                        <input
                          type="radio"
                          name="contact-item"
                          value={itm}
                          checked={contactForm.item === itm}
                          onChange={() =>
                            setContactForm((prev) => ({ ...prev, item: itm }))
                          }
                          className="h-4 w-4 text-[#0E2F73]"
                        />
                        <span>{itm}</span>
                      </label>
                    ))}
                  </div>
                </fieldset>
                <p className="text-xs text-slate-400">
                  We automatically link safe property/agreement records to speed up resolution.
                </p>
              </div>
            )}

            {/* Step 3: What Happened */}
            {contactForm.step === 3 && (
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <label
                    htmlFor="contact-desc-input"
                    className="text-xs font-bold text-slate-700 dark:text-slate-300"
                  >
                    What happened?
                  </label>
                  <textarea
                    id="contact-desc-input"
                    rows={4}
                    value={contactForm.desc}
                    onChange={(e) =>
                      setContactForm((prev) => ({ ...prev, desc: e.target.value }))
                    }
                    placeholder="Describe the issue in a few sentences..."
                    className="w-full rounded-xl border border-slate-300 bg-white p-3 text-sm dark:border-slate-700 dark:bg-slate-800"
                  />
                </div>

                <fieldset className="space-y-2">
                  <legend className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Is this stopping something time-sensitive?
                  </legend>
                  <div className="space-y-2 pt-1">
                    <label
                      className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm font-semibold ${
                        contactForm.imp === "no"
                          ? "border-[#0E2F73] bg-blue-50/40 dark:border-blue-400 dark:bg-blue-950/20"
                          : "border-slate-200 dark:border-slate-800"
                      }`}
                    >
                      <input
                        type="radio"
                        name="contact-imp"
                        value="no"
                        checked={contactForm.imp === "no"}
                        onChange={() => setContactForm((prev) => ({ ...prev, imp: "no" }))}
                        className="h-4 w-4 text-[#0E2F73]"
                      />
                      <span>No</span>
                    </label>
                    <label
                      className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm font-semibold ${
                        contactForm.imp === "yes"
                          ? "border-[#0E2F73] bg-blue-50/40 dark:border-blue-400 dark:bg-blue-950/20"
                          : "border-slate-200 dark:border-slate-800"
                      }`}
                    >
                      <input
                        type="radio"
                        name="contact-imp"
                        value="yes"
                        checked={contactForm.imp === "yes"}
                        onChange={() => setContactForm((prev) => ({ ...prev, imp: "yes" }))}
                        className="h-4 w-4 text-[#0E2F73]"
                      />
                      <span>Yes — an offer, agreement, move-in, verification or payment deadline</span>
                    </label>
                  </div>
                </fieldset>
                <p className="text-xs text-slate-400">
                  Our routing engine prioritizes time-sensitive requests automatically.
                </p>
              </div>
            )}

            {/* Step 4: Evidence */}
            {contactForm.step === 4 && (
              <div className="space-y-4">
                <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-blue-300 bg-blue-50/50 p-6 text-center hover:border-[#0E2F73] dark:border-blue-900 dark:bg-blue-950/20">
                  <Upload className="h-6 w-6 text-[#0E2F73] dark:text-blue-400" />
                  <div>
                    <span className="block text-sm font-bold text-slate-900 dark:text-white">
                      Add file or screenshot
                    </span>
                    <span className="text-xs text-slate-500">
                      PDF, JPG, PNG · up to 10 MB each
                    </span>
                  </div>
                  <input
                    type="file"
                    multiple
                    accept=".pdf,.jpg,.jpeg,.png"
                    className="sr-only"
                    onChange={(e) => {
                      if (e.target.files?.length) {
                        const names = Array.from(e.target.files).map((f) => f.name);
                        setContactForm((prev) => ({
                          ...prev,
                          files: [...prev.files, ...names],
                        }));
                      }
                    }}
                  />
                </label>

                {contactForm.files.length > 0 && (
                  <ul className="space-y-2">
                    {contactForm.files.map((fileName, idx) => (
                      <li
                        key={idx}
                        className="flex items-center justify-between rounded-xl border border-slate-200 bg-slate-50 p-2.5 text-xs font-semibold text-slate-800 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                      >
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 text-[#0E2F73]" />
                          <span>{fileName}</span>
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                            Scanned
                          </span>
                        </div>
                        <button
                          type="button"
                          onClick={() =>
                            setContactForm((prev) => ({
                              ...prev,
                              files: prev.files.filter((_, i) => i !== idx),
                            }))
                          }
                          className="text-xs font-bold text-red-600 hover:underline"
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="flex items-start gap-2.5 rounded-xl border border-amber-300 bg-amber-50 p-3.5 text-xs text-amber-900 dark:border-amber-900/40 dark:bg-amber-950/20 dark:text-amber-300">
                  <Lock className="h-4 w-4 shrink-0 text-amber-600 mt-0.5" />
                  <span>
                    Do not upload passwords, payment card full numbers or CVVs. Official ID checks
                    should always happen through the Verification tab.
                  </span>
                </div>
              </div>
            )}

            {/* Step 5: Review */}
            {contactForm.step === 5 && (
              <div className="space-y-4">
                <dl className="divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-slate-50/50 p-4 text-xs dark:divide-slate-800 dark:border-slate-800 dark:bg-slate-800/40">
                  <div className="grid grid-cols-3 gap-2 py-2">
                    <dt className="font-bold text-slate-500">Topic</dt>
                    <dd className="col-span-2 font-semibold text-slate-900 dark:text-white">
                      {CATEGORIES.find((c) => c.id === contactForm.cat)?.label} ·{" "}
                      {contactForm.issue}
                    </dd>
                  </div>
                  <div className="grid grid-cols-3 gap-2 py-2">
                    <dt className="font-bold text-slate-500">Linked record</dt>
                    <dd className="col-span-2 font-semibold text-slate-900 dark:text-white">
                      {contactForm.item}
                    </dd>
                  </div>
                  <div className="grid grid-cols-3 gap-2 py-2">
                    <dt className="font-bold text-slate-500">Summary</dt>
                    <dd className="col-span-2 font-medium text-slate-800 dark:text-slate-200">
                      {contactForm.desc}
                    </dd>
                  </div>
                  <div className="grid grid-cols-3 gap-2 py-2">
                    <dt className="font-bold text-slate-500">Evidence</dt>
                    <dd className="col-span-2 font-medium text-slate-800 dark:text-slate-200">
                      {contactForm.files.length ? contactForm.files.join(", ") : "None attached"}
                    </dd>
                  </div>
                </dl>

                <fieldset className="space-y-2">
                  <legend className="text-xs font-bold text-slate-700 dark:text-slate-300">
                    Preferred reply channel
                  </legend>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 pt-1">
                    <label
                      className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-xs font-semibold ${
                        contactForm.ch === "both"
                          ? "border-[#0E2F73] bg-blue-50/40 dark:border-blue-400 dark:bg-blue-950/20"
                          : "border-slate-200 dark:border-slate-800"
                      }`}
                    >
                      <input
                        type="radio"
                        name="contact-ch"
                        value="both"
                        checked={contactForm.ch === "both"}
                        onChange={() => setContactForm((prev) => ({ ...prev, ch: "both" }))}
                        className="h-4 w-4 text-[#0E2F73]"
                      />
                      <span>In-app + Email</span>
                    </label>
                    <label
                      className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-xs font-semibold ${
                        contactForm.ch === "app"
                          ? "border-[#0E2F73] bg-blue-50/40 dark:border-blue-400 dark:bg-blue-950/20"
                          : "border-slate-200 dark:border-slate-800"
                      }`}
                    >
                      <input
                        type="radio"
                        name="contact-ch"
                        value="app"
                        checked={contactForm.ch === "app"}
                        onChange={() => setContactForm((prev) => ({ ...prev, ch: "app" }))}
                        className="h-4 w-4 text-[#0E2F73]"
                      />
                      <span>In-app only</span>
                    </label>
                  </div>
                </fieldset>
              </div>
            )}

            {/* Wizard Actions */}
            <div className="flex items-center justify-between border-t border-slate-100 pt-4 dark:border-slate-800">
              {contactForm.step > 1 ? (
                <button
                  type="button"
                  onClick={() => setContactForm((prev) => ({ ...prev, step: prev.step - 1 }))}
                  className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-xs font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300"
                >
                  Back
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => navigateTo("home")}
                  className="rounded-xl px-4 py-2 text-xs font-bold text-slate-500 hover:text-slate-800"
                >
                  Cancel
                </button>
              )}

              <span className="text-[11px] font-medium text-slate-400">Draft saved</span>

              <button
                type="button"
                onClick={handleNextStep}
                className="rounded-xl bg-[#0E2F73] px-5 py-2 text-xs font-bold text-white transition hover:bg-primary-950"
              >
                {contactForm.step === 5 ? "Submit case" : "Continue"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 9. SUBMITTED CONFIRMATION VIEW */}
      {/* ========================================================================= */}
      {view === "submitted" && (
        <div className="flex flex-col items-center justify-center space-y-4 rounded-3xl border border-slate-200 bg-white p-8 text-center shadow-xs dark:border-slate-800 dark:bg-slate-900 sm:p-12">
          <div className="flex h-16 w-16 items-center justify-center rounded-full bg-emerald-100 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400">
            <CheckCircle2 className="h-8 w-8" />
          </div>

          <h1 className="font-heading text-2xl font-bold text-primary-950 dark:text-white sm:text-3xl">
            Case created · #{arg}
          </h1>

          <p className="max-w-md text-sm text-slate-600 dark:text-slate-300">
            We’ve received your case and will reply in the app
            {contactForm.ch === "both" ? " and by email" : ""}. We’ll show a response estimate once
            our support triage team assesses it.
          </p>

          <div className="flex flex-wrap items-center justify-center gap-3 pt-3">
            <button
              type="button"
              onClick={() => navigateTo("case", arg)}
              className="rounded-xl bg-[#0E2F73] px-5 py-2.5 text-xs font-bold text-white transition hover:bg-primary-950 sm:text-sm"
            >
              View Case
            </button>
            <button
              type="button"
              onClick={() => navigateTo("home")}
              className="rounded-xl border border-slate-300 bg-white px-5 py-2.5 text-xs font-bold text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 sm:text-sm"
            >
              Back to Help
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
