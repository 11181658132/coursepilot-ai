import { readFile } from "node:fs/promises";
import path from "node:path";
import { createHash } from "node:crypto";

// The only deployed index contains five versioned, fictional DEMO101 documents.
export async function demoSource(chunkId: string) {
  if (!/^[a-zA-Z0-9_-]{1,100}$/.test(chunkId)) return null;
  const index = JSON.parse(await readFile(path.join(process.cwd(), "demo-index.json"), "utf8"));
  const chunk = index.chunks.find((row: { chunk_id: string }) => row.chunk_id === chunkId);
  if (!chunk) return null;
  const document = index.documents.find((row: { path: string }) => row.path === chunk.path);
  const filename = path.basename(chunk.path);
  if (!document || !chunk.path.startsWith("DEMO101/") || !index.allowed_files.includes(filename)) return null;
  const content = await readFile(path.join(process.cwd(), "demo-knowledge", filename));
  if (createHash("sha256").update(content).digest("hex") !== document.sha256) return null;
  const { embedding: _embedding, ...source } = chunk;
  void _embedding;
  return { source, content, filename };
}
