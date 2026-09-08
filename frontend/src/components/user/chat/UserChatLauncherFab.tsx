"use client";

import Image from "next/image";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface UserChatLauncherFabProps {
  open: boolean;
  onToggle: () => void;
  hasUnread?: boolean;
}

export function UserChatLauncherFab({ open, onToggle, hasUnread = false }: UserChatLauncherFabProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={open ? "Close Zoiko assistant chat" : "Open Zoiko assistant chat"}
      title="Zoiko Assistant"
      className={cn(
        "fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full shadow-xl shadow-primary-900/30",
        "bg-accent-600 text-white hover:bg-accent-700",
        "transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
        open && "pointer-events-none scale-0 rotate-90 opacity-0",
        !open && "animate-float hover:scale-110 hover:rotate-3 active:scale-95",
        !open && hasUnread && "animate-pulse-ring"
      )}
    >
      <span
        className={cn(
          "absolute transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          open ? "rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100"
        )}
      >
        <Image
          src="/zoikorooms-icon-png.png"
          alt=""
          width={28}
          height={28}
          priority
          className="h-7 w-7 rounded-full object-cover"
        />
      </span>
      <span
        className={cn(
          "absolute transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          open ? "rotate-0 scale-100 opacity-100" : "-rotate-90 scale-0 opacity-0"
        )}
      >
        <X className="h-6 w-6" />
      </span>

      <span className="absolute right-1 top-1 flex h-3 w-3">
        {hasUnread && !open && (
          <span className="absolute inline-flex h-full w-full animate-notification-ping rounded-full bg-accent-400 opacity-75" />
        )}
        <span
          className={cn(
            "relative inline-flex h-3 w-3 rounded-full border-2 border-white",
            hasUnread && !open ? "bg-accent-500" : "bg-emerald-400"
          )}
        />
      </span>
    </button>
  );
}
