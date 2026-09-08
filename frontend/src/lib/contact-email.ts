import { apiClientFetch } from "@/lib/api-client";

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
