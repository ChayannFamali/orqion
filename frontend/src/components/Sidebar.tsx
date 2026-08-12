import { navItems, isNavVisible } from "../lib/nav";
import { cn } from "../lib/utils";

interface SidebarProps {
  activeSection: string;
  onSectionChange: (key: string) => void;
  capabilities: string[];
  collapsed: boolean;
}

export function Sidebar({ activeSection, onSectionChange, capabilities, collapsed }: SidebarProps) {
  const visibleItems = navItems.filter((item) => isNavVisible(item, capabilities));

  return (
    <aside
      className={cn(
        "flex h-full flex-col border-r border-border bg-background transition-all duration-200",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <nav className="flex-1 space-y-1 p-2">
        {visibleItems.map((item) => {
          const Icon = item.icon;
          const isActive = activeSection === item.key;
          return (
            <button
              key={item.key}
              onClick={() => onSectionChange(item.key)}
              title={collapsed ? item.label : undefined}
              className={cn(
                "flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
              )}
            >
              <Icon className="h-5 w-5 shrink-0" />
              {!collapsed && <span>{item.label}</span>}
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
