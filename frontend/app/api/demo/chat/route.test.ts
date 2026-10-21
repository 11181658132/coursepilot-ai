import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createHmac } from "node:crypto";
import { GET, POST } from "./route";

const SECRET = "test-salt-never-log-".repeat(2);
const SUCCESS_PAYLOAD = {
  id: "resp_test", output_text: "Grounded answer. \ue200filecite\ue202turn0file0\ue201",
  output: [{ type: "message", content: [{ type: "output_text", text: "Grounded answer.",
    annotations: [{ type: "file_citation", filename: "lecture-01-rag-foundations.md", file_id: "file_test" }] }] },
  { type: "file_search_call", results: [{ filename: "lecture-01-rag-foundations.md", score: 0.94,
    content: [{ type: "text", text: "Retrieved evidence." }] }] }],
  usage: { input_tokens: 1000, output_tokens: 200 },
};
const DEFAULT_BODY = { engine: "file_search", course: "DEMO101", question: "How does RAG work?", material_type: "lecture", history: [], website: "" };
function request(body: Record<string, unknown> = DEFAULT_BODY, headers: Record<string, string> = {}) {
  return new Request("http://localhost:3000/api/demo/chat", { method: "POST", headers: {
    "content-type": "application/json", origin: "http://localhost:3000", "x-forwarded-for": "203.0.113.9", ...headers,
  }, body: JSON.stringify(body) });
}

// External fake DB for gateway unit tests only; PostgreSQL is tested separately.
let count: number;
function mockSuccess(upstream?: () => Promise<Response>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (url, options) => {
    if (String(url).endsWith("/api/rag-limit")) {
      const input = JSON.parse(String(options?.body));
      const allowed = count < 5;
      if (allowed && input.action === "consume") count++;
      return Response.json({ allowed, reason: allowed ? "" : "daily", remaining_today: Math.max(0, 5 - count), remaining_month: 300 - count,
        retry_at: allowed ? null : "2026-10-23T00:00:00Z", retry_after_seconds: allowed ? 0 : 60 });
    }
    return upstream ? upstream() : Response.json(SUCCESS_PAYLOAD, { headers: { "x-request-id": "req_openai_test" } });
  });
}
beforeEach(() => {
  vi.restoreAllMocks(); count = 0;
  vi.stubEnv("OPENAI_API_KEY", "test-key-never-log");
  vi.stubEnv("OPENAI_DEMO_VECTOR_STORE_ID", "vs_test");
  vi.stubEnv("DEMO_RATE_LIMIT_SALT", SECRET);
  vi.stubEnv("DATABASE_URL", "postgresql://test.invalid/demo");
  vi.spyOn(console, "info").mockImplementation(() => undefined);
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllEnvs(); });

describe("durable public RAG gateway", () => {
  it("reads quota without model calls or consuming attempts", async () => {
    const fetcher = mockSuccess();
    const result = await GET(new Request("http://localhost:3000/api/demo/chat"));
    expect(result.status).toBe(200); expect((await result.json()).remaining_today).toBe(5);
    expect(result.headers.get("Cache-Control")).toBe("no-store");
    expect(count).toBe(0); expect(fetcher).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).action).toBe("status");
  });
  it("never forwards unexpected quota identities or worker debug fields", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ allowed: true, reason: "", remaining_today: 5, remaining_month: 300,
      retry_at: null, retry_after_seconds: 0, debug: "SECRET", quota_key: "private-digest" }));
    const result = await GET(new Request("http://localhost:3000/api/demo/chat"));
    const text = await result.text();
    expect(text).not.toContain("SECRET"); expect(text).not.toContain("private-digest");
  });
  it("defaults to hybrid only after durable reservation, signing both internal calls", async () => {
    vi.stubEnv("OPENAI_DEMO_VECTOR_STORE_ID", "");
    const fetcher = mockSuccess(async () => Response.json({ answer: "Hybrid answer", citations: [], evidence: [], engine: "hybrid" }));
    const result = await POST(request({ course: "DEMO101", material_type: "all", question: "What is RAG?" }));
    expect(result.status).toBe(200); expect((await result.json()).engine).toBe("hybrid");
    const [quotaUrl, quotaOptions] = fetcher.mock.calls[0];
    expect(String(quotaUrl)).toMatch(/\/api\/rag-limit$/);
    const qHeaders = quotaOptions!.headers as Record<string, string>;
    expect(qHeaders["X-Limit-Signature"]).toBe(createHmac("sha256", SECRET).update(`rag-limit-v1.${qHeaders["X-Limit-Timestamp"]}.${quotaOptions!.body}`).digest("hex"));
    expect(JSON.parse(String(quotaOptions!.body))).toEqual({ action: "consume", quota_key: expect.stringMatching(/^[a-f0-9]{64}$/) });
    expect(String(quotaOptions!.body)).not.toContain("203.0.113.9");
    expect(String(quotaOptions!.body)).not.toContain("What is RAG?");
    const [url, options] = fetcher.mock.calls[1];
    expect(String(url)).toMatch(/\/api\/rag$/);
    const headers = options!.headers as Record<string, string>;
    expect(headers["X-Rag-Signature"]).toBe(createHmac("sha256", SECRET).update(`${headers["X-Rag-Timestamp"]}.${options!.body}`).digest("hex"));
  });
  it("fails closed on missing database or signing secret for BOTH engines", async () => {
    const fetcher = mockSuccess();
    for (const variable of ["DATABASE_URL", "DEMO_RATE_LIMIT_SALT"]) {
      const old = process.env[variable]; vi.stubEnv(variable, "");
      for (const engine of ["hybrid", "file_search"]) expect((await POST(request({ ...DEFAULT_BODY, engine }))).status).toBe(503);
      vi.stubEnv(variable, old);
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("fails closed if quota worker fails or returns malformed data, without provider calls", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({ detail: "SECRET database error" }, { status: 503 }));
    const result = await POST(request());
    expect(result.status).toBe(503); expect(await result.text()).not.toContain("SECRET");
    expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockResolvedValue(Response.json({ allowed: true }));
    expect((await POST(request())).status).toBe(503);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("rejects invalid input, private scope, honeypot and engine before reserving", async () => {
    const fetcher = mockSuccess();
    for (const change of [{ material_type: "private" }, { course: "90049" }, { website: "bot" }, { engine: "other" }, { question: "x".repeat(601) }]) {
      expect((await POST(request({ ...DEFAULT_BODY, ...change }))).status).toBe(400);
    }
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("rejects malformed JSON values", async () => {
    expect((await POST(new Request("http://localhost:3000/api/demo/chat", { method: "POST", headers: { origin: "http://localhost:3000" }, body: "null" }))).status).toBe(400);
  });
  it("ignores forged host headers and blocks cross-site requests", async () => {
    const fetcher = mockSuccess();
    expect((await POST(request(undefined, { origin: "https://attacker.example", "x-forwarded-host": "attacker.example" }))).status).toBe(403);
    expect((await GET(new Request("http://localhost:3000/api/demo/chat", { headers: { "sec-fetch-site": "cross-site" } }))).status).toBe(403);
    expect(fetcher).not.toHaveBeenCalled();
  });
  it("trusts only the platform IP on Vercel, not client-forwarded addresses or cookies", async () => {
    vi.stubEnv("VERCEL", "1"); const fetcher = mockSuccess();
    for (const forged of ["1.1.1.1", "2.2.2.2"]) await POST(request(undefined, { "x-forwarded-for": forged, "x-vercel-forwarded-for": "203.0.113.1", cookie: forged }));
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).quota_key).toBe(JSON.parse(String(fetcher.mock.calls[2][1]?.body)).quota_key);
  });
  it("keeps successful answer parsing, citations, usage and safe telemetry", async () => {
    const fetcher = mockSuccess(); const result = await POST(request()); const data = await result.json();
    expect(data.answer).toBe("Grounded answer."); expect(data.citations).toHaveLength(1); expect(data.evidence).toHaveLength(1);
    expect(data.estimated_cost_usd).toBe(0.0027); expect(data.remaining_today).toBe(4);
    const upstream = JSON.parse(String(fetcher.mock.calls[1][1]?.body));
    expect(upstream.tools[0].filters.type).toBe("and");
    expect(upstream.instructions).toContain("untrusted reference material");
    const telemetry = String(vi.mocked(console.info).mock.calls[0][0]);
    expect(telemetry).not.toContain("How does RAG work?"); expect(telemetry).not.toContain("test-key-never-log");
  });
  it("retains external quota across handler module reloads and blocks the sixth attempt", async () => {
    const fetcher = mockSuccess();
    for (let i = 0; i < 4; i++) expect((await POST(request())).status).toBe(200);
    vi.resetModules(); const reopened = await import("./route");
    expect((await reopened.POST(request())).status).toBe(200);
    const result = await reopened.POST(request()); expect(result.status).toBe(429);
    expect(result.headers.get("Retry-After")).toBe("60");
    expect(fetcher.mock.calls.filter(call => String(call[0]).endsWith("/v1/responses"))).toHaveLength(5);
  });
  it("does not refund a dispatched failure or automatically retry a provider call", async () => {
    let calls = 0;
    const fetcher = mockSuccess(async () => ++calls === 1 ? new Response("{}", { status: 429, headers: { "Retry-After": "30" } }) : Response.json(SUCCESS_PAYLOAD));
    const failed = await POST(request()); expect(failed.status).toBe(429); expect(failed.headers.get("Retry-After")).toBe("30");
    const success = await POST(request()); expect((await success.json()).remaining_today).toBe(3);
    expect(fetcher).toHaveBeenCalledTimes(4); expect(count).toBe(2);
  });
  it("keeps the reservation after an uncertain timeout", async () => {
    mockSuccess(async () => { throw new DOMException("timeout", "TimeoutError"); });
    const result = await POST(request()); expect(result.status).toBe(502);
    expect((await result.json()).remaining_today).toBe(4); expect(count).toBe(1);
  });
});
