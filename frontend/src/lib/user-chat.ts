import { ApiError } from "@/lib/api-client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface UserChatConversation {
  id: number;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface UserChatMessage {
  id: number;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
}

export type UserChatStreamEvent =
  | { type: "text"; text: string }
  | { type: "tool"; name: string }
  | { type: "tool_error"; name: string }
  | { type: "done"; messageId: number; content: string }
  | { type: "error"; message: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      credentials: "include",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    throw new ApiError(0, "Server disconnected. Please make sure the backend is running and try again.");
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? `Request to ${path} failed with ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function listUserChatConversations(): Promise<UserChatConversation[]> {
  return request<UserChatConversation[]>("/api/users/chat/conversations");
}

export function createUserChatConversation(): Promise<UserChatConversation> {
  return request<UserChatConversation>("/api/users/chat/conversations", { method: "POST" });
}

export function listUserChatMessages(conversationId: number): Promise<UserChatMessage[]> {
  return request<UserChatMessage[]>(`/api/users/chat/conversations/${conversationId}/messages`);
}

export function deleteUserChatConversation(conversationId: number): Promise<void> {
  return request<void>(`/api/users/chat/conversations/${conversationId}`, { method: "DELETE" });
}

/** Streams an assistant reply for `content` as UserChatStreamEvent objects. */
export async function* streamUserChatMessage(
  conversationId: number,
  content: string,
  signal?: AbortSignal
): AsyncGenerator<UserChatStreamEvent> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/users/chat/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ content }),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") return;
    throw new ApiError(0, "Server disconnected. Please make sure the backend is running and try again.");
  }

  if (!res.ok || !res.body) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(res.status, body.detail ?? `Streaming failed with ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const processFrame = (rawEvent: string): UserChatStreamEvent | null => {
    let eventType = "";
    let data = "";
    for (const line of rawEvent.split(/\r?\n/)) {
      if (line.startsWith("event: ")) eventType = line.slice(7).trim();
      else if (line.startsWith("data: ")) data += line.slice(6);
    }
    if (!eventType || !data) return null;
    try {
      return { type: eventType, ...JSON.parse(data) } as UserChatStreamEvent;
    } catch {
      return null;
    }
  };

  const drainBuffer = (): UserChatStreamEvent[] => {
    const events: UserChatStreamEvent[] = [];
    let sep = buffer.indexOf("\n\n");
    while (sep !== -1) {
      const rawEvent = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      sep = buffer.indexOf("\n\n");
      const parsed = processFrame(rawEvent);
      if (parsed) events.push(parsed);
    }
    return events;
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    for (const event of drainBuffer()) yield event;
  }

  buffer += decoder.decode();
  for (const event of drainBuffer()) yield event;
}
