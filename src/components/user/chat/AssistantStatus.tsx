"use client";

import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

function subscribe(onChange: () => void) {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

/** "AI Assistant · Online", following the browser's connection state. */
export function AssistantStatus() {
  const online = useSyncExternalStore(
    subscribe,
    () => navigator.onLine,
    () => true
  );
  return (
    <p className="flex items-center gap-1.5 text-xs font-medium" role="status">
      <span aria-hidden="true" className={cn("h-2 w-2 rounded-full", online ? "bg-green-600 dark:bg-green-400" : "bg-slate-400")} />
      <span className="text-slate-600 dark:text-slate-300">AI Assistant</span>
      <span aria-hidden="true" className="text-slate-400">•</span>
      <span className={online ? "text-green-600 dark:text-green-400" : "text-slate-400"}>{online ? "Online" : "Offline"}</span>
    </p>
  );
}
