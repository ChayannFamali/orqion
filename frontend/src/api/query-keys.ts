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
    usage: ["auth", "me", "usage"] as const,
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
    downloadStatus: (providerId: string, jobId: string) =>
      ["providers", providerId, "download-status", jobId] as const,
  },
  roles: {
    all: ["roles"] as const,
  },
  users: {
    all: ["users"] as const,
  },
  corpora: {
    all: ["corpora"] as const,
    available: ["corpora", "available"] as const,
  },
  documents: {
    byCorpus: (corpusId: string) => ["documents", "corpus", corpusId] as const,
  },
  indexVersions: {
    byCorpus: (corpusId: string) => ["index-versions", "corpus", corpusId] as const,
  },
  evalSets: {
    byCorpus: (corpusId: string) => ["eval-sets", "corpus", corpusId] as const,
    detail: (evalSetId: string) => ["eval-sets", "detail", evalSetId] as const,
  },
  evalRuns: {
    bySet: (evalSetId: string) => ["eval-runs", "set", evalSetId] as const,
  },
  analytics: {
    all: ["analytics"] as const,
    range: (start: string, end: string) => ["analytics", start, end] as const,
  },
  audit: {
    all: ["audit"] as const,
    actions: ["audit", "actions"] as const,
  },
  diagnostics: {
    environment: ["diagnostics", "environment"] as const,
  },
  ragSettings: {
    all: ["rag-settings"] as const,
  },
  codeGraph: {
    byCorpus: (corpusId: string) => ["code-graph", corpusId] as const,
  },
  promptTemplates: {
    all: ["prompt-templates"] as const,
  },
} as const;
