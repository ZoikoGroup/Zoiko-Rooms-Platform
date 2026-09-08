"use client";

import { X } from "lucide-react";
import { useEffect, useRef } from "react";

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

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
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

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[200] flex items-start justify-center overflow-y-auto p-4 pt-10 sm:pt-16">
      <button
        aria-label="Close modal"
        onClick={onClose}
        className="animate-fade-in absolute inset-0 bg-primary-900/50 backdrop-blur-sm"
      />
      <div
        className={`animate-scale-in relative w-full ${MAX_WIDTH_CLASS[size]} rounded-2xl bg-white p-6 shadow-2xl dark:bg-slate-900 dark:shadow-black/40`}
      >
        <div className="flex items-center justify-between">
          <h3 className="font-heading text-lg font-bold text-primary-900 dark:text-primary-100">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 dark:text-slate-500 dark:hover:text-slate-300">
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-4" ref={contentRef}>
          {children}
        </div>
      </div>
    </div>
  );
}
