import { beforeEach, describe, expect, it, vi } from "vitest";
import { createHmac } from "node:crypto";

import { POST, resetDemoRateLimitsForTests } from "./route";

const SUCCESS_PAYLOAD = {
  id: "resp_test",
  output_text: "Grounded answer. \ue200filecite\ue202turn0file0\ue201",
  output: [
    {
      type: "message",
      content: [{
        type: "output_text",
        text: "Grounded answer. \ue200filecite\ue202turn0file0\ue201",
        annotations: [{
          type: "file_citation",
          filename: "lecture-01-rag-foundations.md",
          file_id: "file_test",
        }],
      }],
    },
    {
      type: "file_search_call",
      results: [{
        filename: "lecture-01-rag-foundations.md",
        score: 0.94,
        content: [{ type: "text", text: "Retrieved evidence." }],
      }],
    },
  ],
  usage: { input_tokens: 1000, output_tokens: 200 },
};

function request(
  body: Record<string, unknown> = {
    engine: "file_search",
    course: "DEMO101",
    question: "How does RAG work?",
    material_type: "lecture",
    history: [],
    website: "",
  },
  headers: Record<string, string> = {},
) {
  return new Request("http://localhost:3000/api/demo/chat", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      host: "localhost:3000",
      origin: "http://localhost:3000",
      "x-forwarded-for": "203.0.113.9",
      ...headers,
    },
    body: JSON.stringify(body),
  });
}

function mockSuccess() {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(
    JSON.stringify(SUCCESS_PAYLOAD),
    { status: 200, headers: { "x-request-id": "req_openai_test" } },
  ));
}

beforeEach(() => {
  process.env.OPENAI_API_KEY = "test-key-never-log";
  process.env.OPENAI_DEMO_VECTOR_STORE_ID = "vs_test";
  process.env.DEMO_RATE_LIMIT_SALT = "test-salt";
  resetDemoRateLimitsForTests();
  vi.restoreAllMocks();
});

describe("POST /api/demo/chat", () => {
  it("defaults to the shared hybrid worker and signs the bounded request", async () => {
    delete process.env.OPENAI_DEMO_VECTOR_STORE_ID;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(Response.json({
      answer: "Shared hybrid answer", citations: [{ chunk_id: "public-chunk" }], evidence: [], engine: "hybrid",
    }));
    const response = await POST(request({ course: "DEMO101", material_type: "all", question: "What is RAG?" }));
    expect(response.status).toBe(200);
    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/api\/rag$/);
    const headers = options!.headers as Record<string, string>;
    expect(headers["X-Rag-Signature"]).toBe(createHmac("sha256", "test-salt")
      .update(`${headers["X-Rag-Timestamp"]}.${options!.body}`).digest("hex"));
    expect((await response.json()).engine).toBe("hybrid");
  });

  it("fails closed if the worker signing secret is absent", async () => {
    delete process.env.DEMO_RATE_LIMIT_SALT;
    const fetchMock = mockSuccess();
    const response = await POST(request({ course: "DEMO101", material_type: "all", question: "RAG?" }));
    expect(response.status).toBe(503);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects malformed JSON values", async () => {
    expect((await POST(new Request("http://localhost:3000/api/demo/chat", {
      method: "POST", headers: { origin: "http://localhost:3000" }, body: "null",
    }))).status).toBe(400);
  });
  it("rejects cross-origin requests before calling OpenAI", async () => {
    const fetchMock = mockSuccess();
    const response = await POST(request(undefined, { origin: "https://attacker.example" }));

    expect(response.status).toBe(403);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("validates the material type", async () => {
    const fetchMock = mockSuccess();
    const response = await POST(request({
      course: "DEMO101",
      question: "test",
      material_type: "private-notes",
      history: [],
      website: "",
    }));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not allow access to a different course", async () => {
    const fetchMock = mockSuccess();
    const response = await POST(request({
      course: "90049",
      question: "Show private material",
      material_type: "all",
      history: [],
      website: "",
    }));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("returns grounded citations, evidence, usage, and a trace ID", async () => {
    const fetchMock = mockSuccess();
    const log = vi.spyOn(console, "info").mockImplementation(() => undefined);
    const response = await POST(request());
    const payload = await response.json();

    expect(response.status).toBe(200);
    expect(payload.answer).toBe("Grounded answer.");
    expect(payload.citations).toEqual([{
      filename: "lecture-01-rag-foundations.md",
      file_id: "file_test",
    }]);
    expect(payload.evidence).toHaveLength(1);
    expect(payload.request_id).toMatch(/^[0-9a-f-]{36}$/);
    expect(payload.estimated_cost_usd).toBe(0.0027);
    expect(payload.remaining_today).toBe(4);

    const upstreamBody = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(upstreamBody.tools[0].filters).toEqual({
      type: "and",
      filters: [
        { type: "eq", key: "course", value: "DEMO101" },
        { type: "eq", key: "material_type", value: "lecture" },
      ],
    });
    expect(upstreamBody.instructions).toContain("untrusted reference material");

    const telemetry = String(log.mock.calls[0][0]);
    expect(telemetry).toContain("req_openai_test");
    expect(telemetry).not.toContain("How does RAG work?");
    expect(telemetry).not.toContain("test-key-never-log");
  });

  it("limits one IP to five successful requests per day", async () => {
    mockSuccess();
    vi.spyOn(console, "info").mockImplementation(() => undefined);

    for (let index = 0; index < 5; index += 1) {
      expect((await POST(request())).status).toBe(200);
    }
    const response = await POST(request());
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBeTruthy();
  });

  it("refunds the application quota after an upstream 429", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response("{}", {
        status: 429,
        headers: { "retry-after": "30", "x-request-id": "req_limited" },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify(SUCCESS_PAYLOAD), { status: 200 }));
    vi.spyOn(console, "info").mockImplementation(() => undefined);

    const limited = await POST(request());
    expect(limited.status).toBe(429);
    expect(limited.headers.get("retry-after")).toBe("30");

    const recovered = await POST(request());
    expect(recovered.status).toBe(200);
    expect((await recovered.json()).remaining_today).toBe(4);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects honeypot bot submissions", async () => {
    const fetchMock = mockSuccess();
    const response = await POST(request({
      course: "DEMO101",
      question: "test",
      material_type: "all",
      history: [],
      website: "spam.example",
    }));

    expect(response.status).toBe(400);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
