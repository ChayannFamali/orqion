import { useState } from "react";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";
import { navItems, isNavVisible } from "../lib/nav";
import { ChatPage } from "../pages/ChatPage";
import { PlaceholderPage } from "../pages/PlaceholderPage";

interface AppLayoutProps {
  email: string;
  capabilities: string[];
}

export function AppLayout({ email, capabilities }: AppLayoutProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [activeSection, setActiveSection] = useState("chat");

  const visibleItems = navItems.filter((item) => isNavVisible(item, capabilities));
  const activeItem = visibleItems.find((item) => item.key === activeSection);

  const renderContent = () => {
    if (activeSection === "chat") {
      return <ChatPage />;
    }
    return <PlaceholderPage title={activeItem?.label ?? activeSection} />;
  };

  return (
    <div className="flex h-screen flex-col">
      <Topbar email={email} onToggleSidebar={() => setCollapsed((c) => !c)} />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          activeSection={activeSection}
          onSectionChange={setActiveSection}
          capabilities={capabilities}
          collapsed={collapsed}
        />
        <main className="flex-1 overflow-hidden">{renderContent()}</main>
      </div>
    </div>
  );
}
