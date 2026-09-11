const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Many backend endpoints raise HTTPException with a dict `detail`, e.g.
 *  {"message": "...", "reasons": [...]} for eligibility/compliance failures,
 *  or {"message": "...", "reason": "..."} for the occupant-overlap check --
 *  not just a plain string. Passed straight into ApiError/Error's
 *  constructor, an object detail stringifies to the literal text
 *  "[object Object]" instead of anything readable. This pulls out an actual
 *  message (plus any reasons list) from either shape. */
function extractDetailMessage(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { message?: unknown; reasons?: unknown; reason?: unknown };
    const base = typeof d.message === "string" ? d.message : undefined;
    const reasons = Array.isArray(d.reasons)
      ? d.reasons.filter((r): r is string => typeof r === "string")
      : typeof d.reason === "string"
        ? [d.reason]
        : [];
    if (base && reasons.length) return `${base}: ${reasons.join("; ")}`;
    if (base) return base;
    if (reasons.length) return reasons.join("; ");
  }
  return undefined;
}

/** Client-only fetch helper: relies on the browser sending the httpOnly auth cookie. */
export async function apiClientFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // FormData bodies (file uploads) must let the browser set their own multipart
  // boundary header -- forcing application/json here would break the upload.
  const isFormData = init?.body instanceof FormData;

  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      credentials: "include",
      headers: {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(0, "Server disconnected. Please make sure the backend is running and try again.");
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, extractDetailMessage(body.detail) ?? `Request to ${path} failed with ${res.status}`);
  }

  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
