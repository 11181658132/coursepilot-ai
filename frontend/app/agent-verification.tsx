import { Verification } from "../lib/agent-verification";

export default function AgentVerification({ report }: { report?: Verification }) {
  if (!report) return <p className="notice">旧记录没有执行后校验报告，确认时服务器仍会检查来源；请人工核对目标覆盖和内容。</p>;
  const names: Record<string, string> = { task_constraints: "任务天数、时长与初始进度", current_evidence: "当前来源与原文引文", plan_version: "计划版本", progress_and_edit_constraints: "已完成进度与调整范围" };
  return <section className="notice" aria-label="Agent 执行后校验">
    <strong>{report.status === "passed" ? "结构与来源校验通过 · 仍需人工核对" : "执行后校验未通过 · 不可确认"}</strong>
    <ul>{report.checks.map(check => <li key={check.id}>{names[check.id] ?? check.id}：{check.status === "passed" ? "通过" : "未通过"}</li>)}</ul>
    <p>上次检查：{new Date(report.checked_at).toLocaleString()}。目标覆盖、引用是否支持结论、答案是否正确，仍需你阅读原文判断；这不是模型准确率或自动评分。确认时会重新检查来源。</p>
  </section>;
}
