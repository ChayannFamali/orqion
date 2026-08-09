import { cn } from "../lib/utils";
import type { ConversationResponse } from "../api/types";

interface ConversationListProps {
  conversations: ConversationResponse[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
}: ConversationListProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-border p-3">
        <button
          onClick={onNew}
          className="w-full rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Новый диалог
        </button>
      </div>
      <div className="flex-1 overflow-y-auto">
        {conversations.length === 0 ? (
          <p className="p-3 text-sm text-muted-foreground">Нет диалогов</p>
        ) : (
          <ul className="space-y-0.5 p-2">
            {conversations.map((conv) => (
              <li key={conv.id}>
                <button
                  onClick={() => onSelect(conv.id)}
                  className={cn(
                    "w-full truncate rounded-md px-3 py-2 text-left text-sm transition-colors",
                    activeId === conv.id
                      ? "bg-secondary text-secondary-foreground"
                      : "hover:bg-secondary/60",
                  )}
                  title={conv.title || "Без заголовка"}
                >
                  {conv.title || "Без заголовка"}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
