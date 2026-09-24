import { apiClientFetch } from "@/lib/api-client";
import { AppNotification, NotificationPreference } from "@/lib/types";

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

// Section 11 gap: category opt-out + quiet hours, shared by both topbars the
// same way the four functions above are.
export function getNotificationPreferences(basePath: string): Promise<NotificationPreference> {
  return apiClientFetch<NotificationPreference>(`${basePath}/preferences`);
}

export function updateNotificationPreferences(basePath: string, payload: NotificationPreference): Promise<NotificationPreference> {
  return apiClientFetch<NotificationPreference>(`${basePath}/preferences`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
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
    if (notificationType.startsWith("offer.")) return "/leasing";
    if (notificationType.startsWith("agreement.")) return "/leasing";
    if (notificationType.startsWith("identity_verification.")) return "/trust-safety";
    if (notificationType.startsWith("sublet_request.")) return "/occupancy";
    if (notificationType.startsWith("dispute.")) return "/finance";
    return null;
  }

  // recipient === "user"
  if (notificationType === "listing.published" || notificationType === "listing.rejected") {
    return "/account/host/listings";
  }
  // The Host's own "Applications to review" surface (ZR-ENG-CLR-011 Section 10).
  if (notificationType === "application.received" || notificationType === "application.decided") {
    return "/account/host/applications";
  }
  // Host-facing variants of renter-facing events -- distinct notificationType
  // strings (the "_for_host"/"_host" suffix) so they never share a route with
  // the renter's own copy of a conceptually similar event.
  if (
    notificationType === "offer.accepted_for_host" ||
    notificationType === "offer.declined_for_host" ||
    notificationType === "agreement.signed_for_host" ||
    notificationType === "occupancy.move_in_confirmed_for_host" ||
    notificationType === "occupancy.ended_for_host" ||
    notificationType === "payout.paid" ||
    notificationType === "payout.held" ||
    notificationType === "dispute.opened_for_host" ||
    notificationType === "dispute.resolved_for_host"
  ) {
    return "/account/host/listings";
  }
  // Sublet notifications that only ever go to the host (backend/app/crud/sublet.py
  // sends them to the listing's party) -- the host decides sublet requests, so
  // they open the host review page, never the renter's own /account/sublets.
  if (
    notificationType === "sublet_request.submitted" ||
    notificationType === "sublet_request.tenant_response_submitted" ||
    notificationType === "sublet_request.withdrawn" ||
    notificationType === "sublet_request.tenant_changed"
  ) {
    return "/account/host/sublet-requests";
  }
  if (notificationType.startsWith("application.")) return "/account/applications";
  if (notificationType.startsWith("offer.")) return "/account/applications";
  if (notificationType.startsWith("agreement.")) return "/account/applications";
  if (notificationType.startsWith("identity_verification.")) return "/account/identity";
  if (notificationType.startsWith("occupancy.")) return "/account/rentals";
  if (notificationType.startsWith("deposit.")) return "/account/payments";
  if (notificationType.startsWith("refund.")) return "/account/payments";
  if (notificationType.startsWith("sublet_request.")) return "/account/sublets";
  // Payout/dispute have no renter-facing detail page today -- mark read only
  // rather than sending the renter somewhere that won't show them anything.
  return null;
}
