import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { MarkdownRenderer } from "../components/MarkdownRenderer";

describe("MarkdownRenderer", () => {
  it("renders plain text markdown", () => {
    const { container } = render(<MarkdownRenderer content="Hello world" />);
    expect(container.textContent).toContain("Hello world");
  });

  it("renders markdown formatting (bold, code)", () => {
    const { container } = render(
      <MarkdownRenderer content="**bold** and `code`" />,
    );
    expect(container.querySelector("strong")).not.toBeNull();
    expect(container.querySelector("code")).not.toBeNull();
  });

  it("renders headings on their own line", () => {
    const { container } = render(
      <MarkdownRenderer content="# Heading" />,
    );
    expect(container.querySelector("h1")).not.toBeNull();
  });

  it("sanitizes dangerous HTML — strips script tag entirely", () => {
    const content = "<script>alert('xss')</script>text";
    const { container } = render(<MarkdownRenderer content={content} />);
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).not.toContain("<script>");
  });

  it("sanitizes dangerous HTML — strips img onerror handler", () => {
    const content = '<img src="x" onerror="alert(1)" alt="test" />';
    const { container } = render(<MarkdownRenderer content={content} />);
    const img = container.querySelector("img");
    // img may be stripped entirely or kept without event handlers
    if (img) {
      expect(img.getAttribute("onerror")).toBeNull();
    }
  });

  it("renders GFM tables", () => {
    const markdown = "| A | B |\n|---|---|\n| 1 | 2 |";
    const { container } = render(<MarkdownRenderer content={markdown} />);
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelectorAll("td").length).toBe(2);
  });

  it("renders links as anchor tags with rel=noopener noreferrer", () => {
    const { container } = render(
      <MarkdownRenderer content="[orqion](https://github.com/nicescy/orqion)" />,
    );
    const link = container.querySelector("a");
    expect(link).not.toBeNull();
    expect(link?.getAttribute("href")).toBe("https://github.com/nicescy/orqion");
    expect(link?.getAttribute("rel")).toContain("noopener");
    expect(link?.getAttribute("rel")).toContain("noreferrer");
  });

  // Gate 3 — sanitization tests for malicious content from RAG documents

  it("sanitizes javascript: protocol in markdown links", () => {
    const content = "[click me](javascript:alert(1))";
    const { container } = render(<MarkdownRenderer content={content} />);
    const link = container.querySelector("a");
    if (link) {
      const href = link.getAttribute("href") ?? "";
      expect(href).not.toContain("javascript:");
    }
  });

  it("sanitizes javascript: protocol in markdown images", () => {
    const content = "![x](javascript:alert(1))";
    const { container } = render(<MarkdownRenderer content={content} />);
    const img = container.querySelector("img");
    if (img) {
      const src = img.getAttribute("src") ?? "";
      expect(src).not.toContain("javascript:");
    }
  });

  it("sanitizes HTML injection in img tag via markdown context", () => {
    // Model might quote document content verbatim, including HTML
    const content = '<img src=x onerror="alert(document.cookie)">';
    const { container } = render(<MarkdownRenderer content={content} />);
    const img = container.querySelector("img");
    if (img) {
      expect(img.getAttribute("onerror")).toBeNull();
    }
  });

  it("sanitizes data: protocol in img src (potential XSS vector)", () => {
    const content = '![x](data:text/html,<script>alert(1)</script>)';
    const { container } = render(<MarkdownRenderer content={content} />);
    const img = container.querySelector("img");
    if (img) {
      const src = img.getAttribute("src") ?? "";
      expect(src).not.toContain("<script>");
    }
  });
});
