import { demoSource } from "../../../../../../lib/demo-sources";

export const runtime = "nodejs";
export async function GET(_request: Request, context: { params: Promise<{ chunkId: string }> }) {
  const result = await demoSource((await context.params).chunkId);
  if (!result) return Response.json({ detail: "原文不存在。" }, { status: 404 });
  return new Response(new Uint8Array(result.content), { headers: {
    "Content-Type": "text/markdown; charset=utf-8",
    "Content-Disposition": `attachment; filename="${result.filename}"`,
    "X-Content-Type-Options": "nosniff",
  } });
}
