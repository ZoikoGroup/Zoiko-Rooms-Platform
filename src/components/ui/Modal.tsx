"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef } from "react";

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

const MAX_WIDTH_CLASS = {
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
} as const;

export function Modal({
  open,
  onClose,
  title,
  children,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  /** Defaults to the original narrow, form-sized modal ("md"). Pass "xl" for
   *  content-heavy dialogs like a full listing detail view or a multi-step wizard. */
  size?: keyof typeof MAX_WIDTH_CLASS;
}) {
  const contentRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // WCAG 2.2 AA: keep keyboard focus inside the dialog while it's open.
      if (e.key === "Tab" && dialogRef.current) {
        const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (!open || !contentRef.current) return;
    // A form's own overflow-y-auto container can end up scrolled (e.g. by
    // browser autofill jumping to a field) -- always reset it to the top on open.
    const scrollable = contentRef.current.querySelector<HTMLElement>(".overflow-y-auto");
    scrollable?.scrollTo({ top: 0 });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    // Move focus into the dialog on open, and give it back to whatever
    // triggered it on close -- a sighted keyboard user must never be left
    // with focus on a control that's no longer visible.
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const firstFocusable = dialogRef.current?.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
    (firstFocusable ?? dialogRef.current)?.focus();
    return () => previouslyFocused?.focus();
  }, [open]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center overflow-y-auto p-4 pt-10 sm:pt-16">
      <button
        aria-label="Close modal"
        onClick={onClose}
        className="animate-fade-in absolute inset-0 bg-primary-900/50 backdrop-blur-sm"
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`animate-scale-in relative w-full ${MAX_WIDTH_CLASS[size]} rounded-2xl bg-white p-6 shadow-2xl outline-none dark:bg-slate-900 dark:shadow-black/40`}
      >
        <div className="flex items-center justify-between">
          <h3 id={titleId} className="font-heading text-lg font-bold text-primary-900 dark:text-primary-100">{title}</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>
        <div className="mt-4" ref={contentRef}>
          {children}
        </div>
      </div>
    </div>
  );
}
