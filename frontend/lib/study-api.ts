export type StudyCitation = { filename: string; source_id?: string; quote: string; chunk_id?: string; page?: number; locator_type?: "page" | "slide" | "section" };
export type StudyTask = { id: string; day: number; title: string; minutes: number; study_goal: string; quiz_question: string; quiz_answer: string; citations: StudyCitation[]; done: boolean };
export type StudyPlan = {
  id: string; course: string; title?: string; status: "running" | "draft" | "active" | "failed"; version: number;
  request: { goal: string; days: number; daily_minutes: number };
  tasks: StudyTask[]; trace: { step: number; tool: string; status: string; evidence_count?: number }[];
  accounted_cost_usd: number; uncertain_calls: number; error_code?: string;
};

export async function studyRequest<T>(base: string, path: string, init?: RequestInit, demoMode = false): Promise<T> {
  const response = await fetch(`${demoMode ? "/api/demo" : `${base}/api`}/study-plans${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store",
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = typeof data?.detail === "string" ? data.detail : "操作失败，请刷新后重试。";
    throw new Error(response.status === 409 ? `${message}（状态冲突）` : message);
  }
  return data as T;
}
