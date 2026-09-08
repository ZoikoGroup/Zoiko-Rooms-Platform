"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader } from "@/components/ui/Loader";
import { UserProfile } from "@/lib/types";
import { getCurrentUser } from "@/lib/user-auth";
import { UserSessionProvider } from "@/components/user/UserSessionContext";

/**
 * Client-side gate for the /account area. Mirrors AdminGuard, but resolves the
 * session through `/api/users/me` (the `zoiko_user_token` cookie) and bounces to the
 * user login page -- an admin session must never satisfy this guard, and vice versa.
 */
export function UserGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [user, setUser] = useState<UserProfile | null>(null);

  useEffect(() => {
    getCurrentUser().then((profile) => {
      if (profile) {
        setUser(profile);
      } else {
        router.replace("/account/login");
      }
    });
  }, [router]);

  if (!user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
        <Loader label="Verifying your session" />
      </div>
    );
  }

  return <UserSessionProvider user={user}>{children}</UserSessionProvider>;
}
