import { afterEach, describe, expect, it, vi } from "vitest";
import { requestError } from "./request-error";
import { studyRequest } from "./study-api";

afterEach(() => vi.unstubAllGlobals());
describe("request recovery guidance", () => {
  it("explains shared quotas without promising refresh restores them", () => {
    expect(requestError(429, "", "12")).toContain("至少等待 12 秒");
    expect(requestError(429)).toContain("刷新不会恢复额度");
    expect(requestError(429, "", "invalid")).not.toContain("NaN");
  });
  it("distinguishes conflicts, lost sessions, and uncertain upstream outcomes", () => {
    expect(requestError(409)).toContain("先刷新");
    expect(requestError(401)).toContain("会话已失效");
    expect(requestError(503, "private error")).toContain("尚不能确认操作结果");
    expect(requestError(503, "private error")).not.toContain("private error");
    expect(requestError(400, "检查输入")).toBe("检查输入");
  });
  it("never retries failed model mutations", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "limit" }), { status: 429, headers: { "Retry-After": "20" } }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(studyRequest("", "", { method: "POST", body: "{}" }, true)).rejects.toThrow("20 秒");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/demo/study-plans");
  });
  it("treats a broken connection as an unknown outcome without replay", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    await expect(studyRequest("", "", { method: "POST" }, true)).rejects.toThrow("尚不能确认操作结果");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
