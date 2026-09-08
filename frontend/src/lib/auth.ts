import { apiClientFetch } from "@/lib/api-client";
import { AdminRole } from "@/lib/types";

export interface AdminProfile {
  id: number;
  email: string;
  fullName: string;
  phone: string;
  role: AdminRole;
}

export function login(email: string, password: string): Promise<AdminProfile> {
  return apiClientFetch<AdminProfile>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export async function logout(): Promise<void> {
  await apiClientFetch("/api/auth/logout", { method: "POST" });
}

export async function getCurrentAdmin(): Promise<AdminProfile | null> {
  try {
    return await apiClientFetch<AdminProfile>("/api/auth/me");
  } catch {
    return null;
  }
}
