// Package the canonical Python RAG implementation for Vercel's project root.
// Generated files are checked in so Python and Next builders see the same inputs.
import { readFile, mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const check = process.argv.includes("--check");
const modules = [
  "__init__.py", "config.py", "models.py", "services/__init__.py",
  "services/chunk_store.py", "services/documents.py", "services/hybrid_kb.py",
  "services/library.py", "services/openai_kb.py", "services/parsing.py",
  "services/retrieval.py", "services/state.py", "services/telemetry.py",
  "services/study_agent.py", "services/study_store.py", "services/public_study_store.py",
  "services/study_feedback.py",
];
for (const name of modules) {
  const content = await readFile(path.join(root, "backend/app", name));
  const target = path.join(root, "frontend/_rag_backend/app", name);
  if (check) {
    const existing = await readFile(target).catch(() => Buffer.from(""));
    if (!content.equals(existing)) throw new Error(`RAG bundle is stale: ${name}`);
  } else {
    await mkdir(path.dirname(target), { recursive: true });
    await writeFile(target, content);
  }
}
console.log(`Canonical RAG bundle ${check ? "verified" : "prepared"} (${modules.length} modules).`);
