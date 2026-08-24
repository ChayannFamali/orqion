import type { LucideIcon } from "lucide-react";
import { MessageSquare, Database, Activity, BarChart3, Server, Users, ScrollText, Shield, Cpu } from "lucide-react";

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
];

/** Проверяет, доступен ли пункт навигации по capabilities пользователя. */
export function isNavVisible(item: NavItem, capabilities: string[]): boolean {
  if (item.capability === undefined) {
    return true;
  }
  return capabilities.includes("*") || capabilities.includes(item.capability);
}
