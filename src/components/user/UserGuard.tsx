"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader } from "@/components/ui/Loader";
import { SessionChangedNotice } from "@/components/ui/SessionChangedNotice";
import { UserProfile } from "@/lib/types";
import { getCurrentUser } from "@/lib/user-auth";
import { claimTabIdentity, watchForForeignSessionChange } from "@/lib/session-guard";
import { UserSessionProvider } from "@/components/user/UserSessionContext";

/**
 * Client-side gate for the /account area. Mirrors AdminGuard, but resolves the
 * session through `/api/users/me` (the `zoiko_user_token` cookie) and bounces to the
 * user login page -- an admin session must never satisfy this guard, and vice versa.
 */
export function UserGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<UserProfile | null>(null);
  const [sessionChangedElsewhere, setSessionChangedElsewhere] = useState(false);

  useEffect(() => {
    getCurrentUser().then((profile) => {
      if (profile) {
        // zoiko_user_token is shared by every tab in this browser. If another
        // tab logged in as a different account and *this* tab is now loading
        // (or reloading), the identity we just fetched won't match what this
        // tab last recorded -- catches the swap even on a plain reload, not
        // just while this tab stays live (see watchForForeignSessionChange
        // below for that case).
        const { changed } = claimTabIdentity("user", profile.id);
        if (changed) {
          setSessionChangedElsewhere(true);
        }
        setUser(profile);
      } else {
        router.replace("/account/login");
      }
    });
  }, [router]);

  // Covers the live case: this tab stays mounted (no reload) while a
  // different account logs in/out on another tab.
  useEffect(() => {
    if (!user) return;
    return watchForForeignSessionChange("user", () => setSessionChangedElsewhere(true));
  }, [user]);

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
        <Loader label="Verifying your session" />
      </div>
    );
  }

  if (sessionChangedElsewhere) {
    return <SessionChangedNotice />;
  }

  return <UserSessionProvider user={user}>{children}</UserSessionProvider>;
}
