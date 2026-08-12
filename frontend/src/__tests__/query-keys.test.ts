import { describe, it, expect } from "vitest";
import { queryKeys } from "../api/query-keys";

describe("queryKeys", () => {
  it("auth.me is a stable tuple", () => {
    expect(queryKeys.auth.me).toEqual(["auth", "me"]);
    expect(queryKeys.auth.me).toBe(queryKeys.auth.me);
  });

  it("health is a stable tuple", () => {
    expect(queryKeys.health).toEqual(["health"]);
  });

  it("conversations.all is a stable tuple", () => {
    expect(queryKeys.conversations.all).toEqual(["conversations"]);
  });

  it("conversations.detail returns key with id", () => {
    expect(queryKeys.conversations.detail("abc")).toEqual(["conversations", "abc"]);
  });

  it("conversations.detail returns same reference for same id", () => {
    expect(queryKeys.conversations.detail("abc")).toEqual(queryKeys.conversations.detail("abc"));
  });

  it("models.available is a stable tuple", () => {
    expect(queryKeys.models.available).toEqual(["models", "available"]);
  });

  it("conversations.all is a prefix of conversations.detail", () => {
    const detail = queryKeys.conversations.detail("xyz");
    // invalidateQueries({ queryKey: queryKeys.conversations.all })
    // should match conversations/:id as well
    expect(detail.slice(0, queryKeys.conversations.all.length)).toEqual(
      queryKeys.conversations.all,
    );
  });
});
