"use client";

import { useEffect, useRef } from "react";
import { Check } from "lucide-react";
import { CHAT_LANGUAGES, type ChatLanguage } from "@/components/user/chat/chat-language";

interface ChatLanguageMenuProps {
  open: boolean;
  language: ChatLanguage;
  label: string;
  onSelect: (code: ChatLanguage) => void;
  onClose: () => void;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
}

/** The header's language picker. It only changes the assistant, not the rest of the app. */
export function ChatLanguageMenu({ open, language, label, onSelect, onClose, triggerRef }: ChatLanguageMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
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

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label={label}
      className="absolute right-3 top-full z-50 mt-1 w-44 overflow-hidden rounded-xl border border-slate-200 bg-white shadow-lg dark:border-white/10 dark:bg-slate-900"
    >
      {CHAT_LANGUAGES.map(({ code, label: name }) => (
        <button
          key={code}
          type="button"
          role="menuitemradio"
          aria-checked={language === code}
          onClick={() => {
            onSelect(code);
            onClose();
          }}
          className="flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none dark:text-slate-200 dark:hover:bg-white/5 dark:focus-visible:bg-white/5"
        >
          <span>{name}</span>
          {language === code && <Check aria-hidden="true" className="h-4 w-4 text-primary-700 dark:text-primary-300" />}
        </button>
      ))}
    </div>
  );
}
