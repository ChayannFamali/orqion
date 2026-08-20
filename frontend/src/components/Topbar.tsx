import { Menu, Sun, Moon, LogOut } from "lucide-react";
import { Button } from "./ui/button";
import { useTheme } from "../hooks/useTheme";
import { useLogout } from "../hooks/useAuth";
import { UsageWidget } from "./UsageWidget";

interface TopbarProps {
  email: string;
  onToggleSidebar: () => void;
}

export function Topbar({ email, onToggleSidebar }: TopbarProps) {
  const { theme, toggle } = useTheme();
  const logout = useLogout();

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-background px-4">
      <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="Свернуть панель">
        <Menu className="h-5 w-5" />
      </Button>

      <div className="flex items-center gap-2">
        <Button variant="ghost" size="icon" onClick={toggle} aria-label="Переключить тему">
          {theme === "dark" ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
        </Button>
        <UsageWidget />
        <span className="text-sm text-muted-foreground">{email}</span>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => logout.mutate()}
          disabled={logout.isPending}
          aria-label="Выйти"
        >
          <LogOut className="h-5 w-5" />
        </Button>
      </div>
    </header>
  );
}
