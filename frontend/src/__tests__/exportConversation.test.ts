/**
 * Tests for conversation export to markdown (T-429).
 */

import { describe, expect, it, vi } from "vitest";

import type { ConversationDetailResponse, MessageResponse } from "../api/types";
import {
  conversationToMarkdown,
  downloadMarkdown,
  sanitizeFilename,
} from "../utils/exportConversation";

const ORIGIN = "http://localhost:5173";

function makeMessage(
  role: string,
  content: string,
  meta: Record<string, unknown> = {},
): MessageResponse {
  return {
    id: `msg-${role}-${Math.random()}`,
    role,
    content,
    model_id: null,
    tokens_in: null,
    tokens_out: null,
    created_at: "2026-08-22T12:00:00Z",
    meta,
  };
}

function makeConversation(
  title: string,
  messages: MessageResponse[],
): ConversationDetailResponse {
  return {
    id: "conv-1",
    title,
    archived: false,
    mode: "chat",
    created_at: "2026-08-22T12:00:00Z",
    message_count: messages.length,
    messages,
  };
}

describe("conversationToMarkdown", () => {
  it("generates markdown with title and messages", () => {
    const conv = makeConversation("Test Dialog", [
      makeMessage("user", "Hello world"),
      makeMessage("assistant", "Hi there!"),
    ]);

    const md = conversationToMarkdown(conv, ORIGIN);

    expect(md).toContain("# Test Dialog");
    expect(md).toContain("## Вы");
    expect(md).toContain("Hello world");
    expect(md).toContain("## Ассистент");
    expect(md).toContain("Hi there!");
  });

  it("includes sources block for assistant with sources", () => {
    const conv = makeConversation("RAG Dialog", [
      makeMessage("user", "What is X?"),
      makeMessage("assistant", "X is Y.", {
        sources: [
          {
            chunk_id: "chunk-1",
            document_id: "doc-abc",
            structural_path: "Chapter 2 > Section 3",
            score: 0.95,
            original_rank: 1,
          },
          {
            chunk_id: "chunk-2",
            document_id: "doc-xyz",
            structural_path: "Appendix A",
            score: 0.82,
            original_rank: 2,
          },
        ],
      }),
    ]);

    const md = conversationToMarkdown(conv, ORIGIN);

    expect(md).toContain("**Источники:**");
    expect(md).toContain("Chapter 2 > Section 3");
    expect(md).toContain("Appendix A");
    expect(md).toContain(`${ORIGIN}/api/documents/doc-abc/content`);
    expect(md).toContain(`${ORIGIN}/api/documents/doc-xyz/content`);
  });

  it("skips sources block when meta.sources is missing", () => {
    const conv = makeConversation("No Sources", [
      makeMessage("user", "Hi"),
      makeMessage("assistant", "Hello", {}),
    ]);

    const md = conversationToMarkdown(conv, ORIGIN);

    expect(md).not.toContain("**Источники:**");
    expect(md).toContain("Hello");
  });

  it("skips sources block when sources is empty array", () => {
    const conv = makeConversation("Empty Sources", [
      makeMessage("assistant", "Response", { sources: [] }),
    ]);

    const md = conversationToMarkdown(conv, ORIGIN);

    expect(md).not.toContain("**Источники:**");
  });

  it("handles old conversations without meta at all", () => {
    const conv = makeConversation("Old Dialog", [
      makeMessage("user", "Old question"),
      makeMessage("assistant", "Old answer"),
    ]);

    const md = conversationToMarkdown(conv, ORIGIN);

    expect(md).toContain("# Old Dialog");
    expect(md).toContain("Old question");
    expect(md).toContain("Old answer");
    expect(md).not.toContain("**Источники:**");
  });

  it("skips unknown roles", () => {
    const conv = makeConversation("Mixed", [
      makeMessage("user", "User message"),
      makeMessage("system", "System prompt"),
      makeMessage("assistant", "Assistant response"),
    ]);

    const md = conversationToMarkdown(conv, ORIGIN);

    expect(md).toContain("## Вы");
    expect(md).toContain("## Ассистент");
    expect(md).not.toContain("System");
  });
});

describe("sanitizeFilename", () => {
  it("replaces special characters with dashes", () => {
    const result = sanitizeFilename("My: Dialog / Test?");
    expect(result).toMatch(/^My-Dialog-Test-\d{4}-\d{2}-\d{2}\.md$/);
  });

  it("preserves Cyrillic characters", () => {
    const result = sanitizeFilename("Мой диалог");
    expect(result).toContain("Мой-диалог");
  });

  it("collapses multiple dashes", () => {
    const result = sanitizeFilename("A---B");
    expect(result).toMatch(/^A-B-\d{4}-\d{2}-\d{2}\.md$/);
  });

  it("falls back to 'conversation' for empty title", () => {
    const result = sanitizeFilename("");
    expect(result).toContain("conversation");
  });

  it("appends date", () => {
    const result = sanitizeFilename("Test");
    expect(result).toMatch(/^Test-\d{4}-\d{2}-\d{2}\.md$/);
  });

  it("truncates to 50 chars before date", () => {
    const longTitle = "A".repeat(100);
    const result = sanitizeFilename(longTitle);
    const namePart = result.split("-2")[0]; // remove date suffix
    expect(namePart.length).toBeLessThanOrEqual(50);
  });
});

describe("downloadMarkdown", () => {
  it("creates blob and triggers download", () => {
    // jsdom doesn't have URL.createObjectURL — stub it
    vi.stubGlobal("URL", {
      ...URL,
      createObjectURL: vi.fn().mockReturnValue("blob:test-url"),
      revokeObjectURL: vi.fn(),
    });

    const clickSpy = vi.fn();

    const anchorMock = {
      href: "",
      download: "",
      style: { display: "" },
      click: clickSpy,
    } as unknown as HTMLAnchorElement;

    vi.spyOn(document, "createElement").mockReturnValue(anchorMock);
    vi.spyOn(document.body, "appendChild").mockImplementation(() => anchorMock);
    vi.spyOn(document.body, "removeChild").mockImplementation(() => anchorMock);

    downloadMarkdown("test.md", "# Hello");

    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test-url");
    expect(anchorMock.download).toBe("test.md");

    vi.unstubAllGlobals();
  });
});
