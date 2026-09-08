import { apiClientFetch } from "@/lib/api-client";
import { AppNotification } from "@/lib/types";

/**
 * Shared client for the notification bell, used by both the Admin and USER
 * topbars against their own base path -- the backend scopes every query to the
 * authenticated caller, so there's no cross-role data risk in sharing this file.
 */
export const ADMIN_NOTIFICATIONS_BASE = "/api/notifications";
export const USER_NOTIFICATIONS_BASE = "/api/users/notifications";

export function listNotifications(basePath: string): Promise<AppNotification[]> {
  return apiClientFetch<AppNotification[]>(basePath);
}

export function getUnreadNotificationCount(basePath: string): Promise<{ count: number }> {
  return apiClientFetch<{ count: number }>(`${basePath}/unread-count`);
}

export function markNotificationRead(basePath: string, id: number): Promise<AppNotification> {
  return apiClientFetch<AppNotification>(`${basePath}/${id}/read`, { method: "PATCH" });
}

export function markAllNotificationsRead(basePath: string): Promise<{ updated: number }> {
  return apiClientFetch<{ updated: number }>(`${basePath}/read-all`, { method: "PATCH" });
}

/**
 * Where clicking a notification should navigate to. Keyed by `notificationType`
 * (more precise than `relatedEntityType` alone, since e.g. "listing.submitted"
 * needs review by an admin while "listing.published"/"listing.rejected" are the
 * same entity type but belong on the USER's own listings page) and by which
 * topbar it's rendered in, since the same event type is sent to different
 * roles depending on direction (e.g. a host is notified of "listing.published",
 * an admin of "listing.submitted"). Existing pages are reused as-is -- no new
 * routes are introduced. Returns null when there's genuinely nothing to link to
 * (e.g. a welcome notification), in which case clicking only marks it read.
 */
export function resolveNotificationHref(
  notification: Pick<AppNotification, "notificationType" | "relatedEntityId">,
  recipient: "admin" | "user"
): string | null {
  const { notificationType, relatedEntityId } = notification;

  if (recipient === "admin") {
    if (notificationType === "listing.submitted") {
      return relatedEntityId ? `/properties?listingId=${encodeURIComponent(relatedEntityId)}` : "/properties";
    }
    if (notificationType.startsWith("application.")) return "/leasing";
    if (notificationType.startsWith("identity_verification.")) return "/trust-safety";
    if (notificationType.startsWith("sublet_request.")) return "/occupancy";
    return null;
  }

  // recipient === "user"
  if (notificationType === "listing.published" || notificationType === "listing.rejected") {
    return "/account/host/listings";
  }
  if (notificationType === "application.received") return "/account/host/listings";
  if (notificationType.startsWith("application.")) return "/account/applications";
  if (notificationType.startsWith("identity_verification.")) return "/account/identity";
  if (notificationType.startsWith("sublet_request.")) return "/account/sublets";
  return null;
}
