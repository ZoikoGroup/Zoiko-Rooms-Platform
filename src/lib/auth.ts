import { apiClientFetch } from "@/lib/api-client";
import { broadcastSessionChange } from "@/lib/session-guard";
import { AdminRole } from "@/lib/types";

export interface AdminProfile {
  id: number;
  email: string;
  fullName: string;
  phone: string;
  role: AdminRole;
}

/** remember=false: the sign-in lasts only until the browser is closed. */
export async function login(email: string, password: string, remember = true): Promise<AdminProfile> {
  const profile = await apiClientFetch<AdminProfile>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password, remember }),
  });
  // The zoiko_admin_token cookie is shared by every tab in this browser, not
  // just this one -- tell any other open tab a different admin just took it
  // over (see @/lib/session-guard).
  broadcastSessionChange("admin", profile.id);
  return profile;
}

export async function logout(): Promise<void> {
  await apiClientFetch("/api/auth/logout", { method: "POST" });
  broadcastSessionChange("admin", null);
}

export async function getCurrentAdmin(): Promise<AdminProfile | null> {
  try {
    return await apiClientFetch<AdminProfile>("/api/auth/me");
  } catch {
    return null;
  }
}

export function requestAdminPasswordReset(email: string): Promise<{ message: string }> {
  return apiClientFetch<{ message: string }>("/api/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetAdminPassword(token: string, newPassword: string): Promise<{ message: string }> {
  return apiClientFetch<{ message: string }>("/api/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, newPassword }),
  });
}
