import { Menu, LogOut } from "lucide-react";
import { Button } from "./ui/button";
import { useLogout } from "../hooks/useAuth";
import { UsageWidget } from "./UsageWidget";

interface TopbarProps {
  email: string;
  onToggleSidebar: () => void;
}

/**
 * Верхняя панель: переключение сайдбара, использование, выход.
 *
 * Переключателя темы здесь нет (Т-512): оформление — личная настройка,
 * единственный переключатель живёт в разделе «Профиль». Два независимых
 * потребителя темы рассинхронизировались бы — иконка в шапке показывала бы
 * не то состояние, которое выбрал пользователь в профиле.
 */
export function Topbar({ email, onToggleSidebar }: TopbarProps) {
  const logout = useLogout();

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-background px-4">
      <Button variant="ghost" size="icon" onClick={onToggleSidebar} aria-label="Свернуть панель">
        <Menu className="h-5 w-5" />
      </Button>

      <div className="flex items-center gap-2">
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
