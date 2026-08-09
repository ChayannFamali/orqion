import { memo, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Components } from "react-markdown";
import { cn } from "../lib/utils";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

/**
 * Схема санитизации с rel="noopener noreferrer" на всех ссылках.
 *
 * S-33: ссылки — с rel="noopener noreferrer", внешние не выполняются.
 */
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    a: [...(defaultSchema.attributes?.a ?? []), "rel"],
  },
};

/**
 * Безопасный рендер markdown из ответа модели.
 *
 * rehype-sanitize обязателен (AGENTS.md §5.2) — вывод модели
 * является недоверенными данными и не должен интерпретироваться
 * как HTML без санитизации.
 *
 * S-33: подсветка кода — shiki, без исполнения; ссылки — rel="noopener noreferrer".
 */
export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className,
}: MarkdownRendererProps) {
  return (
    <div
      className={cn(
        "prose prose-sm max-w-none break-words",
        "prose-pre:bg-muted prose-pre:rounded-md prose-pre:p-3",
        "prose-code:bg-muted prose-code:rounded prose-code:px-1 prose-code:py-0.5",
        "prose-code:before:content-none prose-code:after:content-none",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}
        components={markdownComponents}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
});

/**
 * Компоненты для react-markdown: подсветка кода через shiki (S-33).
 *
 * shiki загружается лениво — один раз на процесс, результат кэшируется.
 */
const codeCache = new Map<string, string>();

const CodeBlock = memo(function CodeBlock({
  className,
  children,
}: {
  className?: string;
  children?: React.ReactNode;
}) {
  const lang = className?.replace("language-", "") ?? "text";
  const code = String(children ?? "");
  const [html, setHtml] = useState<string | null>(codeCache.get(code) ?? null);

  useEffect(() => {
    if (html !== null) return;
    let cancelled = false;
    (async () => {
      try {
        const { codeToHtml } = await import("shiki");
        const highlighted = await codeToHtml(code, {
          lang,
          theme: "github-light",
        });
        if (!cancelled) {
          codeCache.set(code, highlighted);
          setHtml(highlighted);
        }
      } catch {
        if (!cancelled) setHtml(`<pre><code>${escapeHtml(code)}</code></pre>`);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, html, lang]);

  if (html !== null) {
    return (
      <div
        className="overflow-x-auto rounded-md bg-muted p-3"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    );
  }
  return (
    <pre className="rounded-md bg-muted p-3">
      <code>{code}</code>
    </pre>
  );
});

const InlineCode = ({ children }: { children?: React.ReactNode }) => (
  <code className="rounded bg-muted px-1 py-0.5">{children}</code>
);

const Anchor = ({ href, children }: { href?: string; children?: React.ReactNode }) => (
  <a href={href} rel="noopener noreferrer" target="_blank">
    {children}
  </a>
);

const markdownComponents: Components = {
  code: ({ className, children }) => {
    const isBlock = className?.startsWith("language-");
    if (isBlock) {
      return <CodeBlock className={className}>{children}</CodeBlock>;
    }
    return <InlineCode>{children}</InlineCode>;
  },
  a: Anchor as Components["a"],
};

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
