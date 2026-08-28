/**
 * AppLayout: навигация сохраняется в адресе страницы (раздел переживает
 * обновление страницы, работают прямые ссылки и защита по правам).
 */
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { AppLayout, sectionFromHash } from "../components/AppLayout";

vi.mock("../pages/ChatPage", () => ({ ChatPage: () => <div>CHAT_PAGE</div> }));
vi.mock("../pages/CorporaPage", () => ({ CorporaPage: () => <div>CORPORA_PAGE</div> }));
vi.mock("../pages/DiagnosticsPage", () => ({ DiagnosticsPage: () => <div>DIAGNOSTICS_PAGE</div> }));
vi.mock("../pages/ProvidersPage", () => ({ ProvidersPage: () => <div>PROVIDERS_PAGE</div> }));
vi.mock("../pages/RolesPage", () => ({ RolesPage: () => <div>ROLES_PAGE</div> }));
vi.mock("../pages/UsersPage", () => ({ UsersPage: () => <div>USERS_PAGE</div> }));
vi.mock("../pages/AnalyticsPage", () => ({ AnalyticsPage: () => <div>ANALYTICS_PAGE</div> }));
vi.mock("../pages/AuditLogPage", () => ({ AuditLogPage: () => <div>AUDIT_PAGE</div> }));
vi.mock("../pages/TraceListPage", () => ({ TraceListPage: () => <div>TRACES_PAGE</div> }));
vi.mock("../pages/TraceDetailPage", () => ({ TraceDetailPage: () => <div>TRACE_DETAIL</div> }));
vi.mock("../pages/SettingsPage", () => ({ SettingsPage: () => <div>SETTINGS_PAGE</div> }));
vi.mock("../pages/CodeGraphPage", () => ({ CodeGraphPage: () => <div>CODE_GRAPH_PAGE</div> }));
vi.mock("../pages/PlaceholderPage", () => ({ PlaceholderPage: () => <div>PLACEHOLDER</div> }));
vi.mock("../components/Topbar", () => ({ Topbar: () => <div>TOPBAR</div> }));
vi.mock("../hooks/useUsers", () => ({
  useExitImpersonation: vi.fn(),
}));

const ADMIN = ["*"];

function renderLayout(capabilities: string[] = ADMIN) {
  return render(
    <AppLayout
      email="admin@orqion.local"
      capabilities={capabilities}
      isImpersonating={false}
      impersonatedByEmail={null}
    />,
  );
}

describe("sectionFromHash", () => {
  beforeEach(() => {
    window.location.hash = "";
  });

  it("parses known section", () => {
    window.location.hash = "#/corpora";
    expect(sectionFromHash()).toBe("corpora");
  });

  it("falls back to chat on empty or unknown hash", () => {
    window.location.hash = "";
    expect(sectionFromHash()).toBe("chat");
    window.location.hash = "#/bogus";
    expect(sectionFromHash()).toBe("chat");
  });
});

describe("AppLayout navigation persistence", () => {
  beforeEach(() => {
    window.location.hash = "";
  });
  afterEach(() => {
    window.location.hash = "";
  });

  it("opens the section from the URL hash after mount", () => {
    window.location.hash = "#/diagnostics";
    renderLayout();
    expect(screen.getByText("DIAGNOSTICS_PAGE")).toBeInTheDocument();
  });

  it("defaults to chat without a hash and writes the hash back", () => {
    renderLayout();
    expect(screen.getByText("CHAT_PAGE")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/chat");
  });

  it("switching a section updates the URL hash", () => {
    renderLayout();
    fireEvent.click(screen.getByText("Корпуса"));
    expect(screen.getByText("CORPORA_PAGE")).toBeInTheDocument();
    expect(window.location.hash).toBe("#/corpora");
  });

  it("falls back to chat when the hashed section is not permitted", () => {
    window.location.hash = "#/diagnostics";
    // developer-набор без диагностики и провайдеров
    renderLayout(["chat", "upload"]);
    expect(screen.queryByText("DIAGNOSTICS_PAGE")).not.toBeInTheDocument();
    expect(screen.getByText("CHAT_PAGE")).toBeInTheDocument();
  });

  it("T-506: настройки доступны всем без специального права", () => {
    window.location.hash = "#/settings";
    renderLayout(["chat"]);
    expect(screen.getByText("SETTINGS_PAGE")).toBeInTheDocument();
  });

  it("T-504: граф кода недоступен без способности и доступен через *", () => {
    window.location.hash = "#/code-graph";
    const { unmount } = renderLayout(["chat", "upload"]);
    expect(screen.queryByText("CODE_GRAPH_PAGE")).not.toBeInTheDocument();
    expect(screen.getByText("CHAT_PAGE")).toBeInTheDocument();
    unmount();

    window.location.hash = "#/code-graph";
    renderLayout(["*"]);
    expect(screen.getByText("CODE_GRAPH_PAGE")).toBeInTheDocument();
  });
});
