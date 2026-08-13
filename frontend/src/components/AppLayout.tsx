import { useState } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { navItems, isNavVisible } from "../lib/nav";
import { ChatPage } from "../pages/ChatPage";
import { PlaceholderPage } from "../pages/PlaceholderPage";
import { TraceListPage } from "../pages/TraceListPage";
import { TraceDetailPage } from "../pages/TraceDetailPage";
import { ProvidersPage } from "../pages/ProvidersPage";
import { RolesPage } from "../pages/RolesPage";

interface AppLayoutProps {
  email: string;
  capabilities: string[];
}

export function AppLayout({ email, capabilities }: AppLayoutProps) {
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
