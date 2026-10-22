import { describe, expect, it } from "vitest";
import { canConfirm, Verification } from "./agent-verification";

const report: Verification = { version: 1, status: "passed", checked_at: "2026-10-22", checks: [{ id: "current_evidence", status: "passed" }], goal_coverage: "requires_human_review", semantic_support: "requires_human_review" };
describe("Agent confirmation boundary", () => {
  it("requires explicit review even when mechanical checks passed", () => {
    expect(canConfirm(report, false)).toBe(false);
    expect(canConfirm(report, true)).toBe(true);
  });
  it("rejects blocked, inconsistent and unknown-version checks", () => {
    expect(canConfirm({ ...report, status: "blocked" }, true)).toBe(false);
    expect(canConfirm({ ...report, checks: [{ id: "current_evidence", status: "blocked" }] }, true)).toBe(false);
    expect(canConfirm({ ...report, version: 99 }, true)).toBe(false);
    expect(canConfirm({ ...report, checks: [] }, true)).toBe(false);
  });
  it("allows legacy records only after review and server revalidation", () => {
    expect(canConfirm(undefined, false)).toBe(false);
    expect(canConfirm(undefined, true)).toBe(true);
  });
});
