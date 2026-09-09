"use client";

import Image from "next/image";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

interface ChatLauncherFabProps {
  open: boolean;
  onToggle: () => void;
  hasUnread?: boolean;
}

/** Floating action button that opens/closes the AdminChatPanel.
 *
 * Animation states:
 *  - **Idle**: gentle vertical float (animate-float, 4s cycle)
 *  - **Hover**: scale-up + subtle rotate for interactivity feedback
 *  - **Unread**: pulsing accent ring + ping on the status dot
 *  - **Open → closed**: icon morphs from chat-bubble to X with a
 *    scale/rotate transition instead of abruptly disappearing
 *  - All animations respect `prefers-reduced-motion` via globals.css */
export function ChatLauncherFab({ open, onToggle, hasUnread = false }: ChatLauncherFabProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label={open ? "Close Ask Zoiko AI assistant" : "Open Ask Zoiko AI assistant"}
      title="Ask Zoiko · AI assistant"
      className={cn(
        // Base: fixed position, size, colors
        "fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full shadow-xl shadow-primary-900/30",
        // Colors
        "bg-accent-600 text-white hover:bg-accent-700",
        // Transitions: smooth open/close morph
        "transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
        // Open state: shrink + rotate + hide
        open && "pointer-events-none scale-0 rotate-90 opacity-0",
        // Closed state: idle float + hover effects
        !open && "animate-float hover:scale-110 hover:rotate-3 active:scale-95",
        // Unread: pulsing accent ring (replaces idle float while attention-grabbing)
        !open && hasUnread && "animate-pulse-ring"
      )}
    >
      {/* Icon morph: Zoiko logo ↔ close */}
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

      {/* Online status dot — turns into a ping indicator when there's an unread message */}
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
