"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { IdentityVerificationRecord, IdentityVerificationStatus, UserProfile } from "@/lib/types";
import { getCurrentUser } from "@/lib/user-auth";
import { hasVerifiedIdentity, listIdentityVerifications } from "@/lib/user-api";

interface UserSession {
  user: UserProfile | null;
  /** Every document the user has submitted, newest first. */
  identityRecords: IdentityVerificationRecord[];
  /** Status of the most relevant document: a verified one wins, else the newest. */
  identityStatus: IdentityVerificationStatus | "not_submitted";
  identityVerified: boolean;
  loading: boolean;
  refreshUser: () => Promise<UserProfile | null>;
  refreshIdentity: () => Promise<void>;
}

const UserSessionContext = createContext<UserSession | null>(null);

export function UserSessionProvider({
  user: initialUser,
  children,
}: {
  /** Pass null for a page that renders without a login (public browsing) --
      identityVerified stays false and every identity-gated action degrades
      gracefully rather than throwing. */
  user: UserProfile | null;
  children: React.ReactNode;
}) {
  const [user, setUser] = useState<UserProfile | null>(initialUser);
  const [identityRecords, setIdentityRecords] = useState<IdentityVerificationRecord[]>([]);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    const fresh = await getCurrentUser();
    setUser(fresh);
    return fresh;
  }, []);

  const refreshIdentity = useCallback(async () => {
    try {
      setIdentityRecords(await listIdentityVerifications());
    } catch {
      setIdentityRecords([]);
    }
  }, []);

  useEffect(() => {
    refreshIdentity().finally(() => setLoading(false));
  }, [refreshIdentity]);

  const value = useMemo<UserSession>(() => {
    const verified = hasVerifiedIdentity(identityRecords);
    const identityStatus: UserSession["identityStatus"] = verified
      ? "verified"
      : identityRecords[0]?.status ?? "not_submitted";

    return {
      user,
      identityRecords,
      identityStatus,
      identityVerified: verified,
      loading,
      refreshUser,
      refreshIdentity,
    };
  }, [user, identityRecords, loading, refreshUser, refreshIdentity]);

  return <UserSessionContext.Provider value={value}>{children}</UserSessionContext.Provider>;
}

export function useUserSession(): UserSession {
  const ctx = useContext(UserSessionContext);
  if (!ctx) throw new Error("useUserSession must be used inside a UserSessionProvider");
  return ctx;
}
