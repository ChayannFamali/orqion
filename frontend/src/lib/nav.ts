import type { LucideIcon } from "lucide-react";
import { MessageSquare, Database, Activity, BarChart3, Server, Users, ScrollText } from "lucide-react";

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
 *   chat, upload, custom_prompts, manage_corpora, share, view_analytics
 *   "*" (admin wildcard)
 *
 * Capabilities для будущих разделов (manage_providers, manage_users,
 * view_audit, view_traces) ещё не определены в seed-пресетах ролей.
 * Они появятся в T-308+. Пока эти разделы видны только admin (через "*").
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
  { key: "corpora", label: "Корпуса", icon: Database, capability: "manage_corpora" },
  { key: "traces", label: "Трассировки", icon: Activity, capability: "view_traces" },
  { key: "analytics", label: "Аналитика", icon: BarChart3, capability: "view_analytics" },
  { key: "providers", label: "Провайдеры", icon: Server, capability: "manage_providers" },
  { key: "users", label: "Пользователи", icon: Users, capability: "manage_users" },
  { key: "audit", label: "Аудит", icon: ScrollText, capability: "view_audit" },
];

/** Проверяет, доступен ли пункт навигации по capabilities пользователя. */
export function isNavVisible(item: NavItem, capabilities: string[]): boolean {
  if (item.capability === undefined) {
    return true;
  }
  return capabilities.includes("*") || capabilities.includes(item.capability);
}
