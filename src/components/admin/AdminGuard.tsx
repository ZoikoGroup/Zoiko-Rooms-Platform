"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentAdmin } from "@/lib/auth";
import { Loader } from "@/components/ui/Loader";
import { SessionChangedNotice } from "@/components/ui/SessionChangedNotice";
import { claimTabIdentity, watchForForeignSessionChange } from "@/lib/session-guard";

export function AdminGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [adminId, setAdminId] = useState<number | null>(null);
  const [sessionChangedElsewhere, setSessionChangedElsewhere] = useState(false);

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      if (admin) {
        // zoiko_admin_token is shared by every tab in this browser. If
        // another tab logged in as a different admin and *this* tab is now
        // loading (or reloading), the identity we just fetched won't match
        // what this tab last recorded -- catches the swap even on a plain
        // reload, not just while this tab stays live (see
        // watchForForeignSessionChange below for that case).
        const { changed } = claimTabIdentity("admin", admin.id);
        if (changed) {
          setSessionChangedElsewhere(true);
        }
        setAdminId(admin.id);
      } else {
        router.replace("/login");
      }
    });
  }, [router]);

  // Covers the live case: this tab stays mounted (no reload) while a
  // different admin logs in/out on another tab.
  useEffect(() => {
    if (adminId == null) return;
    return watchForForeignSessionChange("admin", () => setSessionChangedElsewhere(true));
  }, [adminId]);

  if (adminId == null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
        <Loader label="Verifying session" />
      </div>
    );
  }

  if (sessionChangedElsewhere) {
    return <SessionChangedNotice />;
  }

  return <>{children}</>;
}
