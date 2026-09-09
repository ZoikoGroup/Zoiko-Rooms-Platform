import { describe, it, expect, vi, beforeEach } from "vitest";
import { streamUserChatMessage } from "@/lib/user-chat";
import { ApiError } from "@/lib/api-client";

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const body = frames.join("\n\n") + "\n\n";
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

describe("streamUserChatMessage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("parses text, tool and done events with handoffSuggested", async () => {
    const frames = [
      "event: text\ndata: {\"text\":\"Checking that for you\"}",
      "event: tool\ndata: {\"name\":\"search_knowledge\"}",
      "event: done\ndata: {\"messageId\":7,\"content\":\"Here is guidance.\",\"guardrail\":{\"risk\":\"R0\",\"risk_topic\":\"\",\"action_tier\":\"A1\",\"determination_blocked\":false},\"handoffSuggested\":true}",
      "event: error\ndata: {\"message\":\"boom\"}",
    ];
    const fetchMock = vi.fn().mockResolvedValue(sseResponse(frames));
    vi.stubGlobal("fetch", fetchMock);

    const events = [];
    for await (const ev of streamUserChatMessage(1, "hello")) {
      events.push(ev);
    }

    expect(fetchMock).toHaveBeenCalledOnce();
    const [, init] = fetchMock.mock.calls[0];
    expect(init.body).toContain("hello");

    expect(events).toHaveLength(4);
    expect(events[0]).toEqual({ type: "text", text: "Checking that for you" });
    expect(events[1]).toEqual({ type: "tool", name: "search_knowledge" });
    const done = events[2] as { type: "done"; messageId: number; handoffSuggested?: boolean };
    expect(done.type).toBe("done");
    expect(done.messageId).toBe(7);
    expect(done.handoffSuggested).toBe(true);
    expect(events[3]).toEqual({ type: "error", message: "boom" });
  });

  it("defaults handoffSuggested to undefined when absent", async () => {
    const frames = ['event: done\ndata: {"messageId":2,"content":"ok"}'];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(frames)));
    const events = [];
    for await (const ev of streamUserChatMessage(1, "hi")) {
      events.push(ev);
    }
    expect((events[0] as { handoffSuggested?: boolean }).handoffSuggested).toBeUndefined();
  });

  it("throws ApiError on non-ok response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "nope" }), { status: 429, headers: { "Content-Type": "application/json" } })
      )
    );
    const gen = streamUserChatMessage(1, "x");
    await expect(gen.next()).rejects.toBeInstanceOf(ApiError);
  });
});
