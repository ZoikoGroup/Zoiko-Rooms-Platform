"use client";

import { AlertTriangle } from "lucide-react";

/**
 * Blocking overlay shown when this tab detects that its session identity was
 * silently replaced by a login/logout in another tab (see
 * @/lib/session-guard). Reloading is the only way out -- it forces every
 * piece of already-rendered state in this tab to be re-fetched under
 * whichever identity the shared browser cookie now actually holds.
 */
export function SessionChangedNotice() {
  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-slate-950/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-2xl bg-white p-6 text-center shadow-xl dark:bg-slate-900">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-amber-100 dark:bg-amber-500/15">
          <AlertTriangle className="h-6 w-6 text-amber-600 dark:text-amber-400" />
        </div>
        <h2 className="mt-4 text-lg font-semibold text-slate-900 dark:text-white">Your session has changed</h2>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">
          A different account signed in on another tab in this browser. Reload this tab to keep viewing the correct
          account.
        </p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-5 inline-flex w-full items-center justify-center rounded-full bg-primary-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-primary-800"
        >
          Reload
        </button>
      </div>
    </div>
  );
}
