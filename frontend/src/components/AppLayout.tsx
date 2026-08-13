import { useState } from "react";
import { AlertTriangle, X } from "lucide-react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { navItems, isNavVisible } from "../lib/nav";
import { ChatPage } from "../pages/ChatPage";
import { PlaceholderPage } from "../pages/PlaceholderPage";
import { TraceListPage } from "../pages/TraceListPage";
import { TraceDetailPage } from "../pages/TraceDetailPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { RolesPage } from "../pages/RolesPage";
import { UsersPage } from "../pages/UsersPage";
import { CorporaPage } from "../pages/CorporaPage";
import { useExitImpersonation } from "../hooks/useUsers";

interface AppLayoutProps {
  email: string;
  capabilities: string[];
  isImpersonating: boolean;
  impersonatedByEmail: string | null;
}

export function AppLayout({
  email,
  capabilities,
  isImpersonating,
  impersonatedByEmail,
}: AppLayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [activeSection, setActiveSection] = useState("chat");
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  const visibleItems = navItems.filter((item) => isNavVisible(item, capabilities));
  const activeItem = visibleItems.find((item) => item.key === activeSection);

  const renderContent = () => {
    if (activeSection === "chat") {
      return <ChatPage />;
    }
    if (activeSection === "providers") {
      return <ProvidersPage />;
    }
    if (activeSection === "roles") {
      return <RolesPage />;
    }
    if (activeSection === "users") {
      return <UsersPage />;
    }
    if (activeSection === "corpora") {
      return <CorporaPage capabilities={capabilities} />;
    }
    if (activeSection === "traces") {
      if (selectedTraceId) {
        return (
          <TraceDetailPage
            traceId={selectedTraceId}
            onBack={() => setSelectedTraceId(null)}
          />
        );
      }
      return <TraceListPage onTraceSelect={setSelectedTraceId} />;
    }
    return <PlaceholderPage title={activeItem?.label ?? activeSection} />;
  };

  return (
    <div className="flex h-screen flex-col">
      <Topbar email={email} onToggleSidebar={() => setCollapsed((c) => !c)} />
      {isImpersonating && <ImpersonationBanner actorEmail={impersonatedByEmail ?? ""} />}
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          activeSection={activeSection}
          onSectionChange={(section) => {
            setActiveSection(section);
            setSelectedTraceId(null);
          }}
          capabilities={capabilities}
          collapsed={collapsed}
        />
        <main className="flex-1 overflow-hidden">{renderContent()}</main>
      </div>
    </div>
  );
}

function ImpersonationBanner({ actorEmail }: { actorEmail: string }) {
  const exitImpersonation = useExitImpersonation();

  return (
    <div className="flex items-center justify-between border-b border-destructive/30 bg-destructive/5 px-4 py-2">
      <div className="flex items-center gap-2 text-sm">
        <AlertTriangle className="h-4 w-4 text-destructive" />
        <span>
          Вы действуете от имени другого пользователя. Вернуться в свою учётную запись:{" "}
          <strong>{actorEmail}</strong>
        </span>
      </div>
      <button
        onClick={() => {
          exitImpersonation.mutate();
          setTimeout(() => window.location.reload(), 500);
        }}
        disabled={exitImpersonation.isPending}
        className="flex items-center gap-1 rounded-md bg-destructive px-3 py-1 text-xs text-destructive-foreground transition-colors hover:bg-destructive/90"
      >
        <X className="h-3 w-3" />
        Выйти из имперсонации
      </button>
    </div>
  );
}
