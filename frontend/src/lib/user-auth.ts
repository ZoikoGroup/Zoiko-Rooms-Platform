import { apiClientFetch } from "@/lib/api-client";
import { UserProfile } from "@/lib/types";

/**
 * USER (renter / host) session helpers.
 *
 * These talk to `/api/users/*`, which the backend authenticates with the
 * `zoiko_user_token` cookie. They are intentionally separate from `@/lib/auth`,
 * which owns the Admin / Super Admin `zoiko_admin_token` session. Both cookies can
 * be present in the same browser at the same time without interfering -- the
 * backend picks the right one by cookie name.
 */

export function userLogin(email: string, password: string): Promise<UserProfile> {
  return apiClientFetch<UserProfile>("/api/users/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function userRegister(
  fullName: string,
  email: string,
  phone: string,
  password: string
): Promise<{ message: string; userId: number | null }> {
  return apiClientFetch<{ message: string; userId: number | null }>("/api/users/register", {
    method: "POST",
    body: JSON.stringify({ fullName, email, phone, password }),
  });
}

export async function userLogout(): Promise<void> {
  await apiClientFetch("/api/users/logout", { method: "POST" });
}

export async function getCurrentUser(): Promise<UserProfile | null> {
  try {
    return await apiClientFetch<UserProfile>("/api/users/me");
  } catch {
    return null;
  }
}

export async function updateUserProfile(fullName: string, phone: string): Promise<UserProfile> {
  await apiClientFetch("/api/users/profile", {
    method: "PUT",
    body: JSON.stringify({ fullName, phone }),
  });
  // PUT /profile has no declared response model on the backend, so read the
  // canonical profile back instead of trusting the update's response shape.
  return apiClientFetch<UserProfile>("/api/users/me");
}

export function changeUserPassword(currentPassword: string, newPassword: string): Promise<{ ok: boolean }> {
  return apiClientFetch<{ ok: boolean }>("/api/users/password", {
    method: "PUT",
    body: JSON.stringify({ currentPassword, newPassword }),
  });
}

/** Always resolves with a generic message, whether or not the email matched an
 *  account -- the backend never reveals which, so don't infer anything from this
 *  succeeding vs. throwing (it only throws on a network/server failure). */
export function requestPasswordReset(email: string): Promise<{ message: string }> {
  return apiClientFetch<{ message: string }>("/api/users/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
}

export function resetPassword(token: string, newPassword: string): Promise<{ message: string }> {
  return apiClientFetch<{ message: string }>("/api/users/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, newPassword }),
  });
}
