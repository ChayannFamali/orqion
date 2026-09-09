import type { LucideIcon } from "lucide-react";
import {
  MessageSquare,
  Bot,
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
  Sparkles,
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
 * manage_skills — enforced на backend (Т-508), не в посевных пресетах,
 * только admin через "*" (паттерн manage_mcp_servers). Сам раздел —
 * админский каталог скиллов; список для выбора в диалоге
 * (/api/skills/available) доступен всем аутентифицированным.
 *
 * manage_agents — enforced на backend (Т-509), не в посевных пресетах,
 * только admin через "*" (паттерн manage_skills). Правит каталог профилей
 * (/api/agent-profiles) и drill-down по чужим диалогам. Раздел «Агенты»
 * при этом виден всем (capability: undefined): старт диалога от профиля —
 * не админское действие, а список выбора (/api/agent-profiles/available)
 * доступен всем аутентифицированным.
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
  // Т-509: каталог профилей агентов и точка входа для старта диалога.
  // Раздел виден ВСЕМ аутентифицированным (capability: undefined): старт
  // диалога от профиля — не админское действие. Кнопки управления
  // (создание/правка/удаление профиля, drill-down по чужим диалогам)
  // скрыты внутри раздела без способности manage_agents, а право
  // проверяется и на сервере (без него — 404 на каталог и записи).
  { key: "agents", label: "Агенты", icon: Bot, capability: undefined },
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
  // Т-508: скиллы — пакеты конфигурации агентного прогона (фрагмент
  // системного промпта + подмножество инструментов). Метка намеренно
  // «Скиллы», а не «Агенты»: раздел «Агенты» зарезервирован Т-509 под
  // каталог переиспользуемых конфигураций. manage_skills — enforced на
  // backend, не в посевных пресетах, только admin через "*".
  {
    key: "skills",
    label: "Скиллы",
    icon: Sparkles,
    capability: "manage_skills",
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
