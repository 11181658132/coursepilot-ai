import { demoSource } from "../../../../../lib/demo-sources";

export const runtime = "nodejs";
export async function GET(_request: Request, context: { params: Promise<{ chunkId: string }> }) {
  const result = await demoSource((await context.params).chunkId);
  return result ? Response.json(result.source) : Response.json({ detail: "原文不存在。" }, { status: 404 });
}
