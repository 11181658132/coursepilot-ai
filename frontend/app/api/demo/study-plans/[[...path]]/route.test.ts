import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET, PATCH, POST } from "./route";

const url = "https://demo.example/api/demo/study-plans";
const context = (path: string[] = []) => ({ params: Promise.resolve({ path }) });
const body = { request_id: "f3d2353a-51b8-4f8e-8abc-0f240eef696a", course: "DEMO101", goal: "Review RAG", days: 3, daily_minutes: 45 };
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.stubEnv("DEMO_AGENT_ENABLED", "true");
  vi.stubEnv("DATABASE_URL", "postgresql://test.invalid/demo");
  vi.stubEnv("DEMO_RATE_LIMIT_SALT", "a".repeat(32));
  fetcher = vi.fn().mockImplementation(async () => new Response("[]"));
  vi.stubGlobal("fetch", fetcher);
});
afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

async function session() {
  const response = await GET(new Request(`${url}?course=DEMO101`), context());
  return response.headers.get("Set-Cookie")!.split(";")[0];
}
function mutation(cookie: string, value: object = body, target = url, method = "POST") {
  return new Request(target, { method, headers: { Cookie: cookie, Origin: "https://demo.example", "Content-Type": "application/json" }, body: JSON.stringify(value) });
}

describe("public Agent gateway", () => {
  it("fails closed when persistence is not configured", async () => {
    vi.stubEnv("DATABASE_URL", "");
    const response = await GET(new Request(`${url}?course=DEMO101`), context());
    expect(response.status).toBe(503);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("establishes a noncacheable anonymous session before allowing writes", async () => {
    const denied = await POST(mutation(""), context());
    expect(denied.status).toBe(401);
    const cookie = await session();
    const response = await POST(mutation(cookie), context());
    expect(response.status).toBe(200);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const envelope = JSON.parse(fetcher.mock.calls.at(-1)![1].body);
    expect(envelope.action).toBe("draft");
    expect(envelope.data).toEqual(body);
    expect(envelope.tenant).toMatch(/^[a-f0-9]{64}$/);
    expect(fetcher.mock.calls.at(-1)![1].headers["X-Agent-Signature"]).toMatch(/^[a-f0-9]{64}$/);
  });
  it("different visitors receive different server namespaces", async () => {
    const first = await session();
    const second = await session();
    await POST(mutation(first), context());
    const one = JSON.parse(fetcher.mock.calls.at(-1)![1].body).tenant;
    await POST(mutation(second), context());
    const two = JSON.parse(fetcher.mock.calls.at(-1)![1].body).tenant;
    expect(one).not.toBe(two);
  });
  it("rejects cross-site mutations before any worker call", async () => {
    const request = new Request(url, { method: "POST", headers: { Origin: "https://evil.example" }, body: "{}" });
    expect((await POST(request, context())).status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("does not derive worker destinations or identity from client JSON/host headers", async () => {
    const cookie = await session();
    const request = mutation(cookie, { ...body, tenant: "forged" });
    request.headers.set("x-forwarded-host", "evil.example");
    await POST(request, context());
    const last = fetcher.mock.calls.at(-1)!;
    expect(last[0]).toBe("https://coursepilot-ai-ebon.vercel.app/api/agent");
    expect(JSON.parse(last[1].body).tenant).not.toBe("forged");
  });
  it("bounds body size and validates routes before forwarding", async () => {
    const cookie = await session(); fetcher.mockClear();
    expect((await POST(mutation(cookie, { goal: "x".repeat(7000) }), context())).status).toBe(400);
    expect((await GET(new Request(`${url}?course=90049`), context())).status).toBe(400);
    expect((await PATCH(mutation(cookie, {}, url, "PATCH"), context(["unknown", "tasks", "bad"])) ).status).toBe(404);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("masks provider/database error bodies and does not retry automatically", async () => {
    const cookie = await session(); fetcher.mockClear();
    fetcher.mockResolvedValue(new Response(JSON.stringify({ detail: "postgresql://SECRET@host/db" }), { status: 500 }));
    const response = await POST(mutation(cookie), context());
    expect(response.status).toBe(503);
    expect(await response.text()).not.toContain("SECRET");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
