import { describe, expect, it } from "vitest";
import { sseUrl } from "./api";

describe("sseUrl", () => {
  it("builds an encoded EventSource URL and omits empty values", () => {
    const value = sseUrl("/api/transport/stream", {
      origin: "上海",
      destination: "杭州 西湖",
      empty: "",
      preference: "high_speed",
    });
    expect(value).toContain("origin=%E4%B8%8A%E6%B5%B7");
    expect(value).toContain("destination=%E6%9D%AD%E5%B7%9E+%E8%A5%BF%E6%B9%96");
    expect(value).not.toContain("empty");
  });
});
