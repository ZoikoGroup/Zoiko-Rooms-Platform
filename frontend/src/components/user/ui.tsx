"use client";

import { useCallback, useState } from "react";
import { CheckCircle2, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Small shared primitives for the /account pages, matching the input, card and toast
 * styling the admin managers already use so the two areas stay visually identical.
 */

export const inputClass =
  "w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm text-slate-800 outline-none ring-1 ring-slate-200 transition-all focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700";

export function Field({
  label,
  hint,
  className,
  children,
}: {
  label: string;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={cn("block", className)}>
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
        {label}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-slate-400">{hint}</span>}
    </label>
  );
}

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <div
      className={cn(
        "rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10",
        className
      )}
    >
      {children}
    </div>
  );
}

export function SectionHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">{title}</h2>
      {subtitle && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
    </div>
  );
}

export function EmptyState({ message }: { message: string }) {
  return <p className="py-10 text-center text-sm text-slate-400">{message}</p>;
}

export type ToastTone = "success" | "error";

export function useToast() {
  const [toast, setToast] = useState<{ message: string; tone: ToastTone } | null>(null);

  const showToast = useCallback((message: string, tone: ToastTone = "success") => {
    setToast({ message, tone });
    setTimeout(() => setToast(null), 3600);
  }, []);

  return { toast, showToast };
}

export function Toast({ toast }: { toast: { message: string; tone: ToastTone } | null }) {
  if (!toast) return null;
  return (
    <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-start gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
      {toast.tone === "success" ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
      ) : (
        <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-accent-400" />
      )}
      <span>{toast.message}</span>
    </div>
  );
}
