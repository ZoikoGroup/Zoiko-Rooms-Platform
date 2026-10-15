import { apiClientFetch } from "@/lib/api-client";
import type { SupportTicket, SupportTicketStatus } from "@/lib/types";

export interface ContactEmail {
  id: number;
  userId: number;
  subject: string;
  message: string;
  isRead: boolean;
  createdAt: string;
  userEmail: string;
  userName: string;
}

export function sendContactEmail(subject: string, message: string): Promise<ContactEmail> {
  return apiClientFetch<ContactEmail>("/api/users/contact", {
    method: "POST",
    body: JSON.stringify({ subject, message }),
  });
}

export function listContactEmails(unreadOnly = false): Promise<ContactEmail[]> {
  const params = unreadOnly ? "?unread=true" : "";
  return apiClientFetch<ContactEmail[]>(`/api/admin/contact-emails${params}`);
}

export function getUnreadCount(): Promise<{ count: number }> {
  return apiClientFetch<{ count: number }>("/api/admin/contact-emails/unread-count");
}

export function markContactEmailRead(emailId: number): Promise<ContactEmail> {
  return apiClientFetch<ContactEmail>(`/api/admin/contact-emails/${emailId}/read`, {
    method: "PUT",
  });
}

// --- Help & Support tickets (support staff side) ---

export interface AdminSupportTicket extends SupportTicket {
  id: number;
  userId: number;
  userEmail: string;
  userName: string;
  assignedAdminId: number | null;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export function listAdminSupportTickets(status?: SupportTicketStatus): Promise<AdminSupportTicket[]> {
  const params = status ? `?status=${status}` : "";
  return apiClientFetch<AdminSupportTicket[]>(`/api/admin/support/tickets${params}`);
}

export function replyAsSupport(ticketId: number, body: string): Promise<AdminSupportTicket> {
  return apiClientFetch<AdminSupportTicket>(`/api/admin/support/tickets/${ticketId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

/** ACTION_REQUIRED needs actionRequest; RESOLVED/CLOSED need resolution. */
export function setSupportTicketStatus(
  ticketId: number,
  status: Exclude<SupportTicketStatus, "SUBMITTED">,
  extra: { actionRequest?: string; resolution?: string } = {},
): Promise<AdminSupportTicket> {
  return apiClientFetch<AdminSupportTicket>(`/api/admin/support/tickets/${ticketId}/status`, {
    method: "POST",
    body: JSON.stringify({ status, ...extra }),
  });
}

export function adminSupportAttachmentUrl(ticketId: number, attachmentId: number): string {
  return `${API_URL}/api/admin/support/tickets/${ticketId}/attachments/${attachmentId}`;
}
