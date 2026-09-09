import { describe, it, expect } from "vitest";
import { navItems, isNavVisible } from "../lib/nav";

describe("isNavVisible", () => {
  it("shows items without capability requirement when capabilities is empty", () => {
    const visible = navItems.filter((item) => isNavVisible(item, []));
    expect(visible.map((i) => i.key)).toEqual(["chat", "agents", "settings"]);
  });

  it("shows only chat and settings for support-level capabilities", () => {
    const visible = navItems.filter((item) => isNavVisible(item, ["chat"]));
    expect(visible.map((i) => i.key)).toEqual(["chat", "agents", "settings"]);
  });

  it("shows chat, corpora and settings for architect-level capabilities", () => {
    const visible = navItems.filter((item) =>
      isNavVisible(item, ["chat", "upload", "custom_prompts", "manage_corpora", "share"]),
    );
    expect(visible.map((i) => i.key)).toEqual(["chat", "agents", "corpora", "settings"]);
  });

  it("shows chat, corpora, analytics and settings for manager-level capabilities", () => {
    const visible = navItems.filter((item) =>
      isNavVisible(item, ["chat", "upload", "custom_prompts", "view_analytics"]),
    );
    expect(visible.map((i) => i.key)).toEqual([
      "chat",
      "agents",
      "corpora",
      "analytics",
      "settings",
    ]);
  });

  it("shows all items for admin wildcard capabilities", () => {
    const visible = navItems.filter((item) => isNavVisible(item, ["*"]));
    expect(visible.map((i) => i.key)).toEqual([
      "chat",
      "agents",
      "corpora",
      "traces",
      "analytics",
      "providers",
      "roles",
      "users",
      "audit",
      "diagnostics",
      "code-graph",
      "document-graph",
      "mcp-servers",
      "skills",
      "settings",
    ]);
  });

  it("T-444: диагностика видна только с view_diagnostics", () => {
    const item = navItems.find((i) => i.key === "diagnostics")!;
    expect(item.capability).toBe("view_diagnostics");
    expect(isNavVisible(item, ["*"])).toBe(true);
    expect(isNavVisible(item, ["view_diagnostics"])).toBe(true);
    expect(isNavVisible(item, ["chat", "manage_providers"])).toBe(false);
    expect(isNavVisible(item, [])).toBe(false);
  });

  it("T-504: граф кода виден только с view_code_graph или *", () => {
    const item = navItems.find((i) => i.key === "code-graph")!;
    expect(item.capability).toBe("view_code_graph");
    expect(isNavVisible(item, ["*"])).toBe(true);
    expect(isNavVisible(item, ["view_code_graph"])).toBe(true);
    expect(isNavVisible(item, ["chat", "view_diagnostics"])).toBe(false);
    expect(isNavVisible(item, [])).toBe(false);
  });

  it("Т-505: граф документов виден только с view_document_graph или *", () => {
    const item = navItems.find((i) => i.key === "document-graph")!;
    expect(item.capability).toBe("view_document_graph");
    expect(isNavVisible(item, ["*"])).toBe(true);
    expect(isNavVisible(item, ["view_document_graph"])).toBe(true);
    expect(isNavVisible(item, ["chat", "view_code_graph"])).toBe(false);
    expect(isNavVisible(item, [])).toBe(false);
  });

  it("Т-503: серверы инструментов видны только с manage_mcp_servers или *", () => {
    const item = navItems.find((i) => i.key === "mcp-servers")!;
    expect(item.capability).toBe("manage_mcp_servers");
    expect(isNavVisible(item, ["*"])).toBe(true);
    expect(isNavVisible(item, ["manage_mcp_servers"])).toBe(true);
    expect(isNavVisible(item, ["chat", "manage_providers"])).toBe(false);
    expect(isNavVisible(item, [])).toBe(false);
  });

  it("Т-508: скиллы видны только с manage_skills или *", () => {
    const item = navItems.find((i) => i.key === "skills")!;
    expect(item.capability).toBe("manage_skills");
    expect(isNavVisible(item, ["*"])).toBe(true);
    expect(isNavVisible(item, ["manage_skills"])).toBe(true);
    expect(isNavVisible(item, ["chat", "manage_mcp_servers"])).toBe(false);
    expect(isNavVisible(item, [])).toBe(false);
  });

  it("Т-508/Т-509: «Скиллы» и «Агенты» — два разных раздела", () => {
    const skills = navItems.find((i) => i.key === "skills")!;
    expect(skills.label).toBe("Скиллы");
    // Скиллы идут сразу за серверами инструментов — оба админских раздела
    // агентной линии рядом.
    const keys = navItems.map((i) => i.key);
    expect(keys.indexOf("skills")).toBe(keys.indexOf("mcp-servers") + 1);
    // Раздел профилей агентов реализован (Т-509) и не совпадает со скиллами.
    expect(keys.indexOf("agents")).not.toBe(keys.indexOf("skills"));
  });

  it("Т-509: раздел «Агенты» виден всем аутентифицированным", () => {
    const item = navItems.find((i) => i.key === "agents")!;
    expect(item.label).toBe("Агенты");
    // Старт диалога от профиля — не админское действие, поэтому у раздела нет
    // требования к способности; управление профилями гейтится на сервере и
    // внутри раздела.
    expect(item.capability).toBeUndefined();
    expect(isNavVisible(item, [])).toBe(true);
    expect(isNavVisible(item, ["chat"])).toBe(true);
    expect(isNavVisible(item, ["*"])).toBe(true);
    // Раздел стоит сразу за чатом: это вторая точка входа в диалог.
    const keys = navItems.map((i) => i.key);
    expect(keys.indexOf("agents")).toBe(keys.indexOf("chat") + 1);
  });

  it("T-506: настройки — последний раздел, видны всем без права", () => {
    const item = navItems[navItems.length - 1];
    expect(item.key).toBe("settings");
    expect(item.capability).toBeUndefined();
    expect(isNavVisible(item, [])).toBe(true);
    expect(isNavVisible(item, ["chat"])).toBe(true);
    expect(isNavVisible(item, ["*"])).toBe(true);
  });

  it("does not show item when capability is missing from list", () => {
    const item = navItems.find((i) => i.key === "roles")!;
    expect(isNavVisible(item, ["chat", "upload"])).toBe(false);
  });

  it("shows item with undefined capability regardless of capabilities", () => {
    const item = navItems.find((i) => i.key === "chat")!;
    expect(isNavVisible(item, [])).toBe(true);
    expect(isNavVisible(item, ["chat"])).toBe(true);
    expect(isNavVisible(item, ["*"])).toBe(true);
  });
});
