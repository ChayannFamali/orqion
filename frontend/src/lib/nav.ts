import type { LucideIcon } from "lucide-react";
import {
  MessageSquare,
  Database,
  Activity,
  BarChart3,
  Server,
  Users,
  ScrollText,
  Shield,
  Cpu,
  GitBranch,
  Network,
  Settings,
  Cable,
} from "lucide-react";

/**
 * Реестр навигационных разделов.
 *
 * Видимость пункта определяется данными (capabilities с сервера),
 * не хардкодом ролей. Логика фильтрации в Sidebar:
 * - capability === undefined → доступен всем
 * - capabilities.includes("*") → доступен всем (admin wildcard)
 * - capabilities.includes(capability) → доступен по праву
 *
 * Точные значения capabilities — из backend/app/policy/presets.py:
 *   chat, upload, custom_prompts, manage_corpora, share, view_analytics,
 *   view_traces, "*" (admin wildcard)
 *
 * manage_providers — enforced на backend (T-308), не в seed presets,
 * только admin через "*".
 *
 * manage_mcp_servers — enforced на backend (Т-503), не в посевных
 * пресетах, только admin через "*" (паттерн manage_providers).
 *
 * Capabilities для будущих разделов (manage_users, view_audit) ещё не
 * определены в seed-пресетах ролей. Они появятся в T-308+.
 * Пока эти разделы видны только admin (через "*").
 */

export interface NavItem {
  key: string;
  label: string;
  icon: LucideIcon;
  /** Право, необходимое для видимости. undefined — доступен всем. */
  capability?: string;
}

export const navItems: NavItem[] = [
  { key: "chat", label: "Чат", icon: MessageSquare, capability: undefined },
  { key: "corpora", label: "Корпуса", icon: Database, capability: "upload" },
  { key: "traces", label: "Трассировки", icon: Activity, capability: "view_traces" },
  { key: "analytics", label: "Аналитика", icon: BarChart3, capability: "view_analytics" },
  { key: "providers", label: "Провайдеры", icon: Server, capability: "manage_providers" },
  { key: "roles", label: "Роли", icon: Shield, capability: "manage_roles" },
  { key: "users", label: "Пользователи", icon: Users, capability: "manage_users" },
  { key: "audit", label: "Аудит", icon: ScrollText, capability: "view_audit" },
  // T-444: только чтение; по умолчанию лишь admin через "*" (не в пресетах)
  { key: "diagnostics", label: "Диагностика", icon: Cpu, capability: "view_diagnostics" },
  // T-504: read-only визуализация графа связей кода; способность не в
  // посевных пресетах — выдаётся через "*" или правкой политики роли.
  { key: "code-graph", label: "Граф кода", icon: GitBranch, capability: "view_code_graph" },
  // Т-505: граф связей документов (семантические кластеры). Отдельная
  // способность по паттерну Т-504 — не в посевных пресетах.
  {
    key: "document-graph",
    label: "Граф документов",
    icon: Network,
    capability: "view_document_graph",
  },
  // Т-503: реестр серверов внешних инструментов (агентные диалоги).
  // manage_mcp_servers — enforced на backend, не в посевных пресетах,
  // только admin через "*" (паттерн manage_providers).
  {
    key: "mcp-servers",
    label: "Серверы инструментов",
    icon: Cable,
    capability: "manage_mcp_servers",
  },
  // T-506: общие настройки (поиск по документам); видны всем, право на
  // изменение проверяется внутри. Будущие вкладки темы/языка — сюда же.
  { key: "settings", label: "Настройки", icon: Settings, capability: undefined },
];

/** Проверяет, доступен ли пункт навигации по capabilities пользователя. */
export function isNavVisible(item: NavItem, capabilities: string[]): boolean {
  if (item.capability === undefined) {
    return true;
  }
  return capabilities.includes("*") || capabilities.includes(item.capability);
}
