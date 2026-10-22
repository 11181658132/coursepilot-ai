export type Verification = { version: number; status: "passed" | "blocked"; checked_at: string;
  checks: { id: string; status: "passed" | "blocked" }[];
  goal_coverage: string; semantic_support: string };

export function canConfirm(report: Verification | undefined, reviewed: boolean) {
  return reviewed && (!report || (report.version === 1 && report.status === "passed" && report.checks.length > 0 && report.checks.every(check => check.status === "passed")));
}
