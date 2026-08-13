import { describe, it, expect } from "vitest";
import { navItems, isNavVisible } from "../lib/nav";

describe("isNavVisible", () => {
  it("shows only items without capability requirement when capabilities is empty", () => {
    const visible = navItems.filter((item) => isNavVisible(item, []));
    expect(visible.map((i) => i.key)).toEqual(["chat"]);
  });

  it("shows only chat for support-level capabilities", () => {
    const visible = navItems.filter((item) => isNavVisible(item, ["chat"]));
    expect(visible.map((i) => i.key)).toEqual(["chat"]);
  });

  it("shows chat and corpora for architect-level capabilities", () => {
    const visible = navItems.filter((item) =>
      isNavVisible(item, ["chat", "upload", "custom_prompts", "manage_corpora", "share"]),
    );
    expect(visible.map((i) => i.key)).toEqual(["chat", "corpora"]);
  });

  it("shows chat and analytics for manager-level capabilities", () => {
    const visible = navItems.filter((item) =>
      isNavVisible(item, ["chat", "upload", "custom_prompts", "view_analytics"]),
    );
    expect(visible.map((i) => i.key)).toEqual(["chat", "analytics"]);
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
    ]);
  });

  it("does not show item when capability is missing from list", () => {
    const item = navItems.find((i) => i.key === "corpora")!;
    expect(isNavVisible(item, ["chat", "upload"])).toBe(false);
  });

  it("shows item with undefined capability regardless of capabilities", () => {
    const item = navItems.find((i) => i.key === "chat")!;
    expect(isNavVisible(item, [])).toBe(true);
    expect(isNavVisible(item, ["chat"])).toBe(true);
    expect(isNavVisible(item, ["*"])).toBe(true);
  });
});
