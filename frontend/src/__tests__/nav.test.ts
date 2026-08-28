import { describe, it, expect } from "vitest";
import { navItems, isNavVisible } from "../lib/nav";

describe("isNavVisible", () => {
  it("shows items without capability requirement when capabilities is empty", () => {
    const visible = navItems.filter((item) => isNavVisible(item, []));
    expect(visible.map((i) => i.key)).toEqual(["chat", "settings"]);
  });

  it("shows only chat and settings for support-level capabilities", () => {
    const visible = navItems.filter((item) => isNavVisible(item, ["chat"]));
    expect(visible.map((i) => i.key)).toEqual(["chat", "settings"]);
  });

  it("shows chat, corpora and settings for architect-level capabilities", () => {
    const visible = navItems.filter((item) =>
      isNavVisible(item, ["chat", "upload", "custom_prompts", "manage_corpora", "share"]),
    );
    expect(visible.map((i) => i.key)).toEqual(["chat", "corpora", "settings"]);
  });

  it("shows chat, corpora, analytics and settings for manager-level capabilities", () => {
    const visible = navItems.filter((item) =>
      isNavVisible(item, ["chat", "upload", "custom_prompts", "view_analytics"]),
    );
    expect(visible.map((i) => i.key)).toEqual(["chat", "corpora", "analytics", "settings"]);
  });

  it("shows all items for admin wildcard capabilities", () => {
    const visible = navItems.filter((item) => isNavVisible(item, ["*"]));
    expect(visible.map((i) => i.key)).toEqual([
      "chat",
      "corpora",
      "traces",
      "analytics",
      "providers",
      "roles",
      "users",
      "audit",
      "diagnostics",
      "code-graph",
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
