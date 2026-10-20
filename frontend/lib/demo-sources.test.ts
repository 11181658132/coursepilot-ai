import { describe, expect, it } from "vitest";
import { demoSource } from "./demo-sources";
import index from "../demo-index.json";

describe("fictional source allowlist", () => {
  it("exposes the original passage but never embeddings", async () => {
    const result = await demoSource(index.chunks[0].chunk_id);
    expect(result!.source.course).toBe("DEMO101");
    expect(result!.source.text).toContain("DEMO101");
    expect(result!.source).not.toHaveProperty("embedding");
    expect(result!.content.toString()).toContain("DEMO101");
  });
  it("does not accept traversal, file paths, or unknown IDs", async () => {
    for (const value of ["../.env", "90049/lecture.pdf", "unknown"]) {
      expect(await demoSource(value)).toBeNull();
    }
  });
});
