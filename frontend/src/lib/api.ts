import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { AdminProfile } from "@/lib/auth";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Server-only fetch helper: forwards the browser's auth cookie to the FastAPI backend. */
export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const cookieStore = await cookies();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      Cookie: cookieStore.toString(),
      ...init?.headers,
    },
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? `Request to ${path} failed with ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

/** Server-only guard: redirects non-super-admins away from a page. Call at the top of the page component.
 *
 *  Only a *confirmed* auth failure (401/403 from the backend) or a successfully-
 *  fetched profile with the wrong role is treated as "not authorized" here. Any
 *  other failure -- a network blip, a 5xx, a malformed response -- is a
 *  transient problem, not a role decision, so it's re-thrown instead of being
 *  silently treated the same as "wrong role" and redirecting the admin away
 *  from the page they asked for (see src/app/(dashboard)/error.tsx, which
 *  catches this and offers a retry instead of a surprise redirect). */
export async function requireSuperAdmin(redirectTo = "/properties"): Promise<AdminProfile> {
  let admin: AdminProfile;
  try {
    admin = await apiFetch<AdminProfile>("/api/auth/me");
  } catch (err) {
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      redirect("/login");
    }
    throw err;
  }
  if (admin.role !== "super_admin") {
    redirect(redirectTo);
  }
  return admin;
}
