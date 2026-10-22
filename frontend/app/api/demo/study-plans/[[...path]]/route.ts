import { createHmac } from "node:crypto";
import { digest, newVisitor, readVisitor, visitorCookie } from "../../../../../lib/demo-session";

export const runtime = "nodejs";
export const maxDuration = 60;
type Context = { params: Promise<{ path?: string[] }> };

function reply(data: unknown, status = 200) {
  return Response.json(data, { status, headers: { "Cache-Control": "no-store", "Vary": "Cookie" } });
}
async function readJson(request: Request): Promise<Record<string, unknown>> {
  const reader = request.body?.getReader();
  if (!reader) throw new Error("Missing body");
  const chunks: Uint8Array[] = []; let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.length;
    if (size > 6000) { await reader.cancel(); throw new Error("Body too large"); }
    chunks.push(value);
  }
  const body = JSON.parse(Buffer.concat(chunks).toString());
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new Error("Invalid body");
  return body;
}

async function handle(request: Request, context: Context) {
  const url = new URL(request.url);
  const origin = request.headers.get("origin");
  const mutating = request.method !== "GET";
  if ((mutating && origin !== url.origin) || (origin && origin !== url.origin)
      || request.headers.get("sec-fetch-site") === "cross-site") {
    return reply({ detail: "请求来源无效。" }, 403);
  }
  const secret = process.env.DEMO_RATE_LIMIT_SALT;
  if (process.env.DEMO_AGENT_ENABLED !== "true" || !secret || secret.length < 32 || !process.env.DATABASE_URL) {
    return reply({ detail: "公开 Agent 尚未启用或数据库未连接。" }, 503);
  }
  const existing = readVisitor(request, secret);
  if (!existing && mutating) return reply({ detail: "访客会话已失效，请刷新页面后重试。" }, 401);
  const visitor = existing ?? newVisitor();
  const path = (await context.params).path ?? [];
  const idPattern = /^v1[a-f0-9]{64}_[a-f0-9]{32}$/;
  let action: string, data: Record<string, unknown>;
  try {
    if (path.length === 0 && request.method === "GET") {
      action = "list"; data = { course: url.searchParams.get("course") };
      if (data.course !== "DEMO101") return reply({ detail: "仅支持 DEMO101。" }, 400);
    } else if (path.length === 0 && request.method === "POST") {
      action = "draft"; data = await readJson(request);
    } else if (path.length === 1 && idPattern.test(path[0]) && request.method === "GET") {
      action = "get"; data = { plan_id: path[0] };
    } else if (path.length === 1 && idPattern.test(path[0]) && request.method === "DELETE") {
      action = "delete"; data = { ...await readJson(request), plan_id: path[0] };
    } else if (path.length === 2 && idPattern.test(path[0]) && ["confirm", "recover"].includes(path[1]) && request.method === "POST") {
      action = path[1]; data = { ...await readJson(request), plan_id: path[0] };
    } else if (path.length === 3 && idPattern.test(path[0]) && path[1] === "tasks" && /^[a-f0-9]{32}$/.test(path[2]) && request.method === "PATCH") {
      action = "task"; data = { ...await readJson(request), plan_id: path[0], task_id: path[2] };
    } else if (path.length === 4 && idPattern.test(path[0]) && path[1] === "tasks" && /^[a-f0-9]{32}$/.test(path[2]) && path[3] === "answers" && request.method === "POST") {
      action = "feedback"; data = { ...await readJson(request), plan_id: path[0], task_id: path[2] };
    } else if (path.length === 4 && idPattern.test(path[0]) && path[1] === "feedbacks" && idPattern.test(path[2]) && path[3] === "confirm" && request.method === "POST") {
      action = "confirm_feedback"; data = { ...await readJson(request), plan_id: path[0], feedback_id: path[2] };
    } else return reply({ detail: "接口不存在。" }, 404);
  } catch { return reply({ detail: "请求格式或大小无效。" }, 400); }
  const tenant = digest(secret, `tenant-v1.${visitor.id}`);
  // Trust only the platform-supplied address on Vercel, never a client JSON identity.
  const ip = process.env.VERCEL ? request.headers.get("x-vercel-forwarded-for") ?? "unknown"
    : request.headers.get("x-forwarded-for") ?? "local";
  const quotaKey = digest(secret, `quota-v1.${ip.split(",")[0].trim()}`);
  const content = JSON.stringify({ tenant, quota_key: quotaKey, action, data });
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`agent-v1.${timestamp}.${content}`).digest("hex");
  const workerUrl = process.env.DEMO_AGENT_URL || `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL || "coursepilot-ai-ebon.vercel.app"}/api/agent`;
  try {
    const upstream = await fetch(workerUrl, { method: "POST", headers: {
      "Content-Type": "application/json", "X-Agent-Timestamp": timestamp, "X-Agent-Signature": signature,
    }, body: content, cache: "no-store", signal: AbortSignal.timeout(55_000) });
    const body = await upstream.json();
    const response = upstream.ok ? reply(body) : reply({ detail: ({
      400: "公开 Agent 请求无效。", 404: "计划或任务不存在。", 409: "状态已变化，请刷新。",
      429: "Agent 次数或费用额度已用完。",
    } as Record<number, string>)[upstream.status] ?? "Agent 暂不可用，请刷新查看已保存的运行。" }, [400, 404, 409, 429].includes(upstream.status) ? upstream.status : 503);
    if (!existing && upstream.ok) response.headers.set("Set-Cookie", visitorCookie(request, secret, visitor));
    return response;
  } catch { return reply({ detail: "连接超时或服务不可用；不会自动重复生成，请刷新查看运行状态。" }, 503); }
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const DELETE = handle;
