import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SourceList } from "../components/SourceList";
import type { ChatSourceEntry } from "../api/types";

function makeSource(overrides: Partial<ChatSourceEntry> = {}): ChatSourceEntry {
  return {
    chunk_id: "chunk-1",
    document_id: "doc-1",
    structural_path: "readme.md › Installation",
    score: 0.95,
    original_rank: 1,
    ...overrides,
  };
}

describe("SourceList", () => {
  it("renders sources with structural_path as link text", () => {
    const sources = [
      makeSource({ structural_path: "guide.md › Setup" }),
      makeSource({
        chunk_id: "chunk-2",
        document_id: "doc-2",
        structural_path: "api.md › Authentication",
      }),
    ];
    render(<SourceList sources={sources} />);

    expect(screen.getByText("guide.md › Setup")).toBeInTheDocument();
    expect(screen.getByText("api.md › Authentication")).toBeInTheDocument();
  });

  it("renders links with correct href to /api/documents/{id}/content", () => {
    const sources = [makeSource({ document_id: "abc-123" })];
    render(<SourceList sources={sources} />);

    const link = screen.getByRole("link");
    expect(link).toHaveAttribute("href", "/api/documents/abc-123/content");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders nothing when sources is empty and not degraded", () => {
    const { container } = render(<SourceList sources={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders degradation warning when ragDegraded is true", () => {
    render(<SourceList sources={[]} ragDegraded={true} ragErrors={["embeddings timeout"]} />);

    expect(screen.getByText(/режиме деградации/)).toBeInTheDocument();
    expect(screen.getByText(/embeddings timeout/)).toBeInTheDocument();
  });

  it("renders degradation warning without errors when ragErrors is empty", () => {
    render(<SourceList sources={[]} ragDegraded={true} />);

    expect(screen.getByText(/режиме деградации/)).toBeInTheDocument();
  });
});
