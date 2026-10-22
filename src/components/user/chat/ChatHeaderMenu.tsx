"use client";

import { useEffect, useRef } from "react";
import { Mail, SquarePen } from "lucide-react";

interface ChatHeaderMenuProps {
  open: boolean;
  onClose: () => void;
  onNewChat: () => void;
  onContact: () => void;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
}

/** The header's "More options" menu. Closes on Escape, outside click or a choice. */
export function ChatHeaderMenu({ open, onClose, onNewChat, onContact, triggerRef }: ChatHeaderMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    menuRef.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        triggerRef.current?.focus();
      }
    };
    const onPointer = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!menuRef.current?.contains(target) && !triggerRef.current?.contains(target)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onPointer);
    };
  }, [open, onClose, triggerRef]);

  if (!open) return null;

  const items = [
    { label: "New conversation", icon: SquarePen, action: onNewChat },
    { label: "Contact admin", icon: Mail, action: onContact },
  ];

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label="More options"
      className="absolute right-3 top-full z-50 mt-1 w-56 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-white/10 dark:bg-slate-900"
    >
      {items.map(({ label, icon: Icon, action }) => (
        <button
          key={label}
          type="button"
          role="menuitem"
          onClick={() => {
            action();
            onClose();
          }}
          className="flex w-full items-center gap-3 px-3 py-2.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none dark:text-slate-200 dark:hover:bg-white/5 dark:focus-visible:bg-white/5"
        >
          <Icon aria-hidden="true" className="h-4 w-4 shrink-0 text-primary-700 dark:text-primary-300" />
          {label}
        </button>
      ))}
    </div>
  );
}
