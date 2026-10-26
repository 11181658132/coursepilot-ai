import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import TaskFeedback from "./study-feedback";
import { StudyFeedback, StudyPlan, StudyTask } from "../lib/study-api";

const task: StudyTask = { id: "task", day: 1, title: "Review", minutes: 30, done: false,
  study_goal: "Review evidence", quiz_question: "Why?", quiz_answer: "Support claims", citations: [] };
const feedback: StudyFeedback = { id: "feedback", task_id: task.id, base_version: 3,
  status: "draft", request: { answer: "My answer" }, accounted_cost_usd: 0, uncertain_calls: 0,
  assessment: { verdict: "needs_review", explanation: "Check the distinction", weaknesses: ["Confusion"], citations: [] },
  adjustments: [] };
function render(entry: StudyFeedback) {
  const plan: StudyPlan = { id: "plan", course: "DEMO101", status: "active", version: 3,
    request: { goal: "Review", days: 1, daily_minutes: 30 }, tasks: [task], trace: [],
    accounted_cost_usd: 0, uncertain_calls: 0, feedback: [entry] };
  return renderToStaticMarkup(<TaskFeedback plan={plan} task={task} apiUrl=""
    demoMode={true} busy={false} onPlan={vi.fn()} onBusy={vi.fn()}
    onError={vi.fn()} onSource={vi.fn()} />);
}
describe("Feedback adjustment safety presentation", () => {
  beforeAll(() => vi.stubGlobal("React", React));
  afterAll(() => vi.unstubAllGlobals());
  it("keeps the assessment visible but explains a blocked adjustment without a confirmation button", () => {
    const html = render({ ...feedback, adjustment_status: "blocked" });
    expect(html).toContain("Check the distinction");
    expect(html).toContain("计划调整未通过检查");
    expect(html).toContain("没有自动重试");
    expect(html).not.toContain("确认应用这份调整");
  });
  it("shows the claimed weakness link as an AI proposal, not a semantic guarantee", () => {
    const html = render({ ...feedback,
      adjustments: [{ task_id: task.id, title: "New review", study_goal: "Practice distinction",
        quiz_question: "Compare", quiz_answer: "Different concepts", citations: [] }],
      adjustment_targets: [{ task_id: task.id, weakness_indices: [0], rationale: "Practice the confusion" }],
    });
    expect(html).toContain("Practice the confusion");
    expect(html).toContain("请核对是否真正相关");
  });
});
