/**
 * Реестр кэш-ключей TanStack Query по доменам.
 *
 * Единая точка для всех ключей: хуки используют queryKeys.* вместо
 * литералов. Это гарантирует стабильность ссылок при invalidateQueries
 * и предотвращает опечатки в строковых литералах.
 *
 * Иерархия: domain → [scope, ...params]. Invalidate по префиксу:
 *   invalidateQueries({ queryKey: queryKeys.conversations.all })
 *   invalidates conversations + conversations/:id
 */
export const queryKeys = {
  auth: {
    me: ["auth", "me"] as const,
  },
  health: ["health"] as const,
  conversations: {
    all: ["conversations"] as const,
    detail: (id: string) => ["conversations", id] as const,
  },
  models: {
    available: ["models", "available"] as const,
  },
  traces: {
    all: ["traces"] as const,
    detail: (id: string) => ["traces", id] as const,
  },
  providers: {
    all: ["providers"] as const,
  },
  roles: {
    all: ["roles"] as const,
  },
  users: {
    all: ["users"] as const,
  },
} as const;
