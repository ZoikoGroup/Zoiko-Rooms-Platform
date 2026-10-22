// @vitest-environment jsdom
import { describe, it, expect, vi } from "vitest";
import { useRef } from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { AssistantHome } from "@/components/user/chat/AssistantHome";
import { AssistantStatus } from "@/components/user/chat/AssistantStatus";
import { ChatHeaderMenu } from "@/components/user/chat/ChatHeaderMenu";
import {
  HOME_CATEGORIES,
  POPULAR_QUESTIONS,
  POPULAR_QUESTIONS_SHOWN,
} from "@/components/user/chat/assistant-home-content";

describe("AssistantHome", () => {
  it("shows the greeting, the six topic cards and six popular questions", () => {
    render(<AssistantHome onAsk={vi.fn()} />);
    expect(screen.getByRole("heading", { name: /Hello! I'm Ask Zoiko/ })).toBeInTheDocument();
    for (const card of HOME_CATEGORIES) {
      expect(screen.getByRole("button", { name: new RegExp(card.title) })).toBeInTheDocument();
    }
    for (const { question } of POPULAR_QUESTIONS.slice(0, POPULAR_QUESTIONS_SHOWN)) {
      expect(screen.getByRole("button", { name: question })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: POPULAR_QUESTIONS[POPULAR_QUESTIONS_SHOWN].question })).toBeNull();
  });

  it("a card or a popular question sends its text", () => {
    const onAsk = vi.fn();
    render(<AssistantHome onAsk={onAsk} />);
    fireEvent.click(screen.getByRole("button", { name: /Payments & listing fees/ }));
    fireEvent.click(screen.getByRole("button", { name: "How do payments work?" }));
    expect(onAsk.mock.calls).toEqual([["How do payments and listing fees work?"], ["How do payments work?"]]);
  });

  it("'See all' reveals every popular question", () => {
    render(<AssistantHome onAsk={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /See all/ }));
    for (const { question } of POPULAR_QUESTIONS) {
      expect(screen.getByRole("button", { name: question })).toBeInTheDocument();
    }
  });

  it("buttons are disabled while a reply is in progress", () => {
    render(<AssistantHome onAsk={vi.fn()} disabled />);
    expect(screen.getByRole("button", { name: /Find a room/ })).toBeDisabled();
  });
});

describe("AssistantStatus", () => {
  it("shows Online, and Offline when the browser loses its connection", () => {
    render(<AssistantStatus />);
    expect(screen.getByText("Online")).toBeInTheDocument();
    const onLine = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    fireEvent(window, new Event("offline"));
    expect(screen.getByText("Offline")).toBeInTheDocument();
    onLine.mockRestore();
  });
});

function MenuHarness({ onNewChat, onContact, onClose }: { onNewChat: () => void; onContact: () => void; onClose: () => void }) {
  const ref = useRef<HTMLButtonElement | null>(null);
  return (
    <>
      <button ref={ref}>More options</button>
      <ChatHeaderMenu open onClose={onClose} onNewChat={onNewChat} onContact={onContact} triggerRef={ref} />
    </>
  );
}

describe("ChatHeaderMenu", () => {
  it("runs the chosen action, closes, and closes on Escape", () => {
    const onNewChat = vi.fn();
    const onContact = vi.fn();
    const onClose = vi.fn();
    render(<MenuHarness onNewChat={onNewChat} onContact={onContact} onClose={onClose} />);

    fireEvent.click(screen.getByRole("menuitem", { name: /Contact admin/ }));
    expect(onContact).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(onNewChat).not.toHaveBeenCalled();
  });
});
