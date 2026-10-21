import { createHmac } from "node:crypto";

export type DemoQuota = {
  allowed: boolean; reason: "" | "daily" | "monthly";
  remaining_today: number; remaining_month: number;
  retry_at: string | null; retry_after_seconds: number;
};

export async function demoQuota(request: Request, consume = false): Promise<DemoQuota> {
  const secret = process.env.DEMO_RATE_LIMIT_SALT;
  if (!secret || secret.length < 32 || !process.env.DATABASE_URL) throw new Error("Quota unavailable");
  // On Vercel only the platform address is authoritative. Missing addresses
  // conservatively share one bucket; client JSON/cookies cannot reset it.
  const ip = (process.env.VERCEL ? request.headers.get("x-vercel-forwarded-for")
    : request.headers.get("x-forwarded-for"))?.split(",")[0].trim() || "unknown";
  const quotaKey = createHmac("sha256", secret).update(`rag-quota-v1.${ip}`).digest("hex");
  const content = JSON.stringify({ action: consume ? "consume" : "status", quota_key: quotaKey });
  const timestamp = String(Math.floor(Date.now() / 1000));
  const signature = createHmac("sha256", secret).update(`rag-limit-v1.${timestamp}.${content}`).digest("hex");
  const destination = process.env.DEMO_RAG_LIMIT_URL
    || `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL || "coursepilot-ai-ebon.vercel.app"}/api/rag-limit`;
  const response = await fetch(destination, { method: "POST", headers: {
    "Content-Type": "application/json", "X-Limit-Timestamp": timestamp, "X-Limit-Signature": signature,
  }, body: content, cache: "no-store", signal: AbortSignal.timeout(8000) });
  if (!response.ok) throw new Error("Quota unavailable");
  const data = await response.json();
  if (!data || typeof data.allowed !== "boolean" || !["", "daily", "monthly"].includes(data.reason)
      || !Number.isInteger(data.remaining_today) || data.remaining_today < 0 || data.remaining_today > 5
      || !Number.isInteger(data.remaining_month) || data.remaining_month < 0 || data.remaining_month > 300
      || !Number.isInteger(data.retry_after_seconds) || data.retry_after_seconds < 0
      || (data.allowed ? data.reason !== "" || data.retry_at !== null || data.retry_after_seconds !== 0
        : !data.reason || typeof data.retry_at !== "string" || !Number.isFinite(Date.parse(data.retry_at)) || data.retry_after_seconds < 1)) {
    throw new Error("Invalid quota result");
  }
  // Whitelist response fields: never forward worker debug metadata or identities.
  return { allowed: data.allowed, reason: data.reason, remaining_today: data.remaining_today,
    remaining_month: data.remaining_month, retry_at: data.retry_at, retry_after_seconds: data.retry_after_seconds };
}
