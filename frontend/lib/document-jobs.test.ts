import { describe, expect, it } from "vitest";
import { uploadQuery, visibleIndexJobs, versionLabel, chooseCourse } from "./document-jobs";

describe("document ingestion boundaries", () => {
  it("selects the first newly uploaded course without replacing an existing selection", () => {
    expect(chooseCourse("", [{ course: "DEMO101" }])).toBe("DEMO101");
    expect(chooseCourse("A", [{ course: "DEMO101" }])).toBe("A");
    expect(chooseCourse("", [])).toBe("");
  });
  it("does not silently opt a file-only upload into paid indexing", () => {
    expect(uploadQuery(" A ", "lecture", "a.md", "hybrid", false).get("index_after_upload")).toBe("false");
    expect(uploadQuery("A", "lecture", "a.md", "file_search", true).get("index_after_upload")).toBe("false");
    expect(uploadQuery("A", "lecture", "a.md", "hybrid", true).get("index_after_upload")).toBe("true");
  });
  it("keeps the running job visible when a follow-up arrives", () => {
    const waiting = { id: "new", status: "queued" }, running = { id: "old", status: "running" };
    expect(visibleIndexJobs([waiting, running])).toEqual({ active: running, waiting: 1 });
    expect(visibleIndexJobs([{ status: "failed" }, waiting])).toEqual({ active: waiting, waiting: 0 });
    expect(visibleIndexJobs([])).toEqual({ active: null, waiting: 0 });
  });
  it("warns about stale citations without inventing a version for legacy replies", () => {
    expect(versionLabel({ document_version: "abc123abc123xyz" })).toBe("资料版本 abc123abc123");
    expect(versionLabel({ document_version: "old", version_status: "stale" })).toContain("重新提问");
    expect(versionLabel({ version_status: "unknown" })).toBe("");
  });
});
