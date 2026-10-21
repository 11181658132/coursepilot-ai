import { afterEach, describe, expect, it, vi } from "vitest";
import { studyRequest } from "./study-api";

afterEach(() => vi.unstubAllGlobals());

describe("study Agent API", () => {
  it("sends an explicit confirmation with the displayed version", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: "active" })));
    vi.stubGlobal("fetch", fetcher);
    const body = JSON.stringify({ confirmed: true, version: 1 });
    const result = await studyRequest("http://localhost:8001", "/plan/confirm", { method: "POST", body });
    expect(result).toEqual({ status: "active" });
    expect(fetcher).toHaveBeenCalledWith("http://localhost:8001/api/study-plans/plan/confirm", expect.objectContaining({ method: "POST", body, cache: "no-store" }));
  });

  it("reports version conflicts without retrying a write", async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "请刷新" }), { status: 409 }));
    vi.stubGlobal("fetch", fetcher);
    await expect(studyRequest("", "/plan/tasks/task", { method: "PATCH" })).rejects.toThrow("状态冲突");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("does not render raw proxy error pages as error messages", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("private upstream error body", { status: 502 })));
    await expect(studyRequest("", "")).rejects.toThrow("操作失败，请刷新后重试。");
  });
});
