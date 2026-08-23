import { useState, useEffect, useRef } from "react";
import { Search, Trash2, X } from "lucide-react";
import { cn } from "../lib/utils";
import type { ConversationResponse } from "../api/types";
import {
  apiSearchConversations,
  type MessageSearchResult,
} from "../api/conversations";

interface ConversationListProps {
  conversations: ConversationResponse[];
  activeId: string | null;
  onSelect: (id: string) => void;
  /** T-443: удаление диалога (кнопка появляется только при переданном колбэке). */
  onDelete?: (id: string) => void;
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onDelete,
}: ConversationListProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MessageSearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  // T-443: инлайн-подтверждение удаления (прецедент CorporaPage — без window.confirm)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Debounce 300ms
  useEffect(() => {
    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }
    debounceRef.current = setTimeout(() => {
      setDebouncedQuery(searchQuery);
    }, 300);
    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
  }, [searchQuery]);

  // Search when debounced query >= 2 chars
  useEffect(() => {
    const q = debouncedQuery.trim();
    if (q.length < 2) {
      setSearchResults([]);
      setIsSearching(false);
      return;
    }
    setIsSearching(true);
    let cancelled = false;
    apiSearchConversations(q)
      .then((results) => {
        if (!cancelled) {
          setSearchResults(results);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSearchResults([]);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsSearching(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery]);

  const isSearchMode = debouncedQuery.trim().length >= 2;

  return (
    // min-h-0 + flex-1 вместо h-full: список занимает остаток aside без
    // переполнения, иначе предок со scroll прокручивается и обрезает шапку
    <div className="flex min-h-0 flex-1 flex-col">
      {/* T-436: строка поиска */}
      <div className="border-b border-border p-2">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Поиск по диалогам…"
            className="w-full rounded-md border border-border bg-background py-1.5 pl-7 pr-7 text-sm placeholder:text-muted-foreground focus:border-primary focus:outline-none"
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery("")}
              className="absolute right-1 top-1/2 -translate-y-1/2 rounded p-0.5 text-muted-foreground hover:text-foreground"
              aria-label="Очистить поиск"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        {isSearchMode ? (
          // Режим поиска
          isSearching ? (
            <p className="p-3 text-sm text-muted-foreground">Поиск…</p>
          ) : searchResults.length === 0 ? (
            <p className="p-3 text-sm text-muted-foreground">
              Ничего не найдено
            </p>
          ) : (
            <ul className="space-y-0.5 p-2">
              {searchResults.map((hit) => (
                <li key={hit.message_id}>
                  <button
                    onClick={() => onSelect(hit.conversation_id)}
                    className={cn(
                      "w-full rounded-md px-3 py-2 text-left text-sm transition-colors",
                      activeId === hit.conversation_id
                        ? "bg-accent text-foreground"
                        : "hover:bg-secondary/60",
                    )}
                  >
                    <div className="truncate font-medium">
                      {hit.content.slice(0, 80)}
                    </div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      {hit.role === "user" ? "Вы" : "Ассистент"}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )
        ) : (
          // Обычный список диалогов
          conversations.length === 0 ? (
            <p className="p-3 text-sm text-muted-foreground">Нет диалогов</p>
          ) : (
            <ul className="space-y-0.5 p-2">
              {conversations.map((conv) => (
                <li key={conv.id}>
                  {confirmDeleteId === conv.id ? (
                    <div
                      className="flex items-center gap-1 rounded-md bg-destructive/10 px-2 py-1.5"
                      data-testid="delete-confirm"
                    >
                      <span className="flex-1 truncate text-xs">
                        Удалить «{conv.title || "Без заголовка"}»?
                      </span>
                      <button
                        onClick={() => {
                          setConfirmDeleteId(null);
                          onDelete?.(conv.id);
                        }}
                        className="shrink-0 rounded bg-destructive px-2 py-0.5 text-xs text-destructive-foreground hover:bg-destructive/90"
                      >
                        Удалить
                      </button>
                      <button
                        onClick={() => setConfirmDeleteId(null)}
                        className="shrink-0 rounded px-2 py-0.5 text-xs text-muted-foreground hover:text-foreground"
                      >
                        Отмена
                      </button>
                    </div>
                  ) : (
                    <div className="group flex items-center gap-1">
                      <button
                        onClick={() => onSelect(conv.id)}
                        className={cn(
                          "min-w-0 flex-1 truncate rounded-md px-3 py-2 text-left text-sm transition-colors",
                          activeId === conv.id
                            ? "bg-accent text-foreground"
                            : "hover:bg-secondary/60",
                        )}
                        title={conv.title || "Без заголовка"}
                      >
                        {conv.title || "Без заголовка"}
                      </button>
                      {onDelete && (
                        <button
                          onClick={() => setConfirmDeleteId(conv.id)}
                          className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100"
                          title="Удалить диалог"
                          aria-label="Удалить диалог"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )
        )}
      </div>
    </div>
  );
}
