/**
 * Export conversation to markdown (T-429).
 *
 * Pure functions for generating markdown from conversation data
 * and triggering a browser download via Blob + objectURL.
 */

import type { ConversationDetailResponse, MessageResponse } from "../api/types";

/** Source entry from message.meta.sources */
interface SourceEntry {
  chunk_id: string;
  document_id: string;
  structural_path: string;
  score: number;
  original_rank: number;
}

/**
 * Generate markdown from a conversation.
 *
 * Only "user" and "assistant" roles are rendered; other roles are skipped.
 * Sources block is included only for assistant messages with non-empty meta.sources.
 *
 * @param conversation - Conversation with messages
 * @param origin - window.location.origin for absolute source URLs
 */
export function conversationToMarkdown(
  conversation: ConversationDetailResponse,
  origin: string,
): string {
  const lines: string[] = [];

  lines.push(`# ${conversation.title}`);
  lines.push("");
  lines.push("---");
  lines.push("");

  for (const msg of conversation.messages) {
    if (msg.role !== "user" && msg.role !== "assistant") {
      continue;
    }

    const roleLabel = msg.role === "user" ? "Вы" : "Ассистент";
    lines.push(`## ${roleLabel}`);
    lines.push("");
    lines.push(msg.content);
    lines.push("");

    if (msg.role === "assistant") {
      const sources = extractSources(msg);
      if (sources.length > 0) {
        lines.push("**Источники:**");
        for (let i = 0; i < sources.length; i++) {
          const src = sources[i];
          const url = `${origin}/api/documents/${src.document_id}/content`;
          lines.push(`${i + 1}. ${src.structural_path} — [открыть](${url})`);
        }
        lines.push("");
      }
    }

    lines.push("---");
    lines.push("");
  }

  return lines.join("\n");
}

/**
 * Extract sources from message.meta, defensively.
 * Returns empty array if meta or sources are missing/invalid.
 */
function extractSources(msg: MessageResponse): SourceEntry[] {
  if (!msg.meta || typeof msg.meta !== "object") {
    return [];
  }
  const sources = (msg.meta as Record<string, unknown>).sources;
  if (!Array.isArray(sources)) {
    return [];
  }
  return sources as SourceEntry[];
}

/**
 * Sanitize a conversation title into a safe filename.
 *
 * Replaces non-alphanumeric chars (Cyrillic preserved) with "-",
 * collapses duplicates, truncates to 50 chars, appends date.
 */
export function sanitizeFilename(title: string): string {
  const date = new Date().toISOString().slice(0, 10);

  let sanitized = title
    .replace(/[^\p{L}\p{N}]/gu, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 50);

  if (!sanitized) {
    sanitized = "conversation";
  }

  return `${sanitized}-${date}.md`;
}

/**
 * Trigger a browser download of markdown content.
 *
 * Creates a Blob, objectURL, temporary anchor, clicks it, then cleans up.
 */
export function downloadMarkdown(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";

  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);

  URL.revokeObjectURL(url);
}
