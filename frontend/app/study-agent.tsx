"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { StudyCitation, StudyPlan, studyRequest } from "../lib/study-api";
import TaskFeedback from "./study-feedback";
import RequestStatus from "./request-status";

type Props = { apiUrl: string; demoMode: boolean; courses: { course: string }[]; onSource: (citation: StudyCitation) => void; initialCourse?: string; initialGoal?: string; onBusyChange?: (busy: boolean) => void };
const statusLabel = { running: "生成中", draft: "待你确认", active: "已确认", failed: "未生成计划" };

export default function StudyAgent({ apiUrl, demoMode, courses, onSource, initialCourse, initialGoal, onBusyChange }: Props) {
  const [course, setCourse] = useState(initialCourse || courses[0]?.course || "");
  const [goal, setGoal] = useState(initialGoal || "根据课程资料，帮我安排考试复习，每天附一道自测题。");
  const [days, setDays] = useState(demoMode ? 3 : 7);
  const [minutes, setMinutes] = useState(60);
  const [plans, setPlans] = useState<StudyPlan[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const call = <T,>(path: string, init?: RequestInit) => studyRequest<T>(apiUrl, path, init, demoMode);
  const pending = useRef<{ key: string; id: string } | null>(null);
  const listVersion = useRef(0);
  const selected = plans.find(plan => plan.id === selectedId);
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);

  useEffect(() => { if (!course && courses.length) setCourse(courses[0].course); }, [courses, course]);
  useEffect(() => {
    setPlans([]); setSelectedId(""); setError(""); setReady(false);
    const version = ++listVersion.current;
    if (!course) return;
    const controller = new AbortController();
    call<StudyPlan[]>(`?course=${encodeURIComponent(course)}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted && version === listVersion.current) { setPlans(data); setSelectedId(data[0]?.id ?? ""); setReady(true); } })
      .catch(reason => { if (!controller.signal.aborted && version === listVersion.current) setError(reason.message); });
    return () => controller.abort();
  }, [course, demoMode, apiUrl]);

  function receive(plan: StudyPlan) {
    ++listVersion.current;
    setPlans(current => [plan, ...current.filter(item => item.id !== plan.id)]);
    setSelectedId(plan.id);
  }

  async function generate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    const key = JSON.stringify({ course, goal, days, daily_minutes: minutes });
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID() };
    try {
      const plan = await call<StudyPlan>("", { method: "POST", body: JSON.stringify({
        request_id: pending.current.id, course, goal, days, daily_minutes: minutes,
      }) });
      receive(plan); pending.current = null;
    } catch (reason) { setError(reason instanceof Error ? reason.message : "连接失败；可复用请求重试。"); }
    finally { setBusy(false); }
  }

  async function mutate(path: string, method: string, body?: object) {
    setBusy(true); setError("");
    try { receive(await call<StudyPlan>(path, { method, body: body ? JSON.stringify(body) : undefined })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作失败"); }
    finally { setBusy(false); }
  }

  return <section className="studyWorkspace" aria-label="复习计划 Agent">
    <div className="managerHeader"><div><div className="sectionLabel">RETRIEVE · PLAN · CONFIRM · TRACK</div>
      <h2>复习计划 Agent</h2><p>查资料、定计划、提交自测，再根据证据反馈调整学习。所有计划改动都由你确认。</p></div></div>
    {demoMode && <div className="notice">仅使用虚构 DEMO101 资料。计划与自测答案按匿名访客隔离并保存在服务器数据库；答案会发送给模型生成反馈。清除 Cookie 或更换浏览器后无法找回。请勿输入真实个人信息。确认和任务更新不调用模型。</div>}
    <>
      <form className="studyForm" onSubmit={generate}>
        <label>课程<select value={course} disabled={busy} onChange={event => setCourse(event.target.value)}>
          {courses.map(item => <option key={item.course} value={item.course}>{item.course}</option>)}
        </select></label>
        <label>复习天数<input type="number" min={1} max={demoMode ? 3 : 14} value={days} disabled={busy} onChange={event => setDays(Number(event.target.value))} /></label>
        <label>每天分钟数<input type="number" min={15} max={demoMode ? 90 : 180} value={minutes} disabled={busy} onChange={event => setMinutes(Number(event.target.value))} /></label>
        <label className="studyGoal">学习目标<textarea value={goal} minLength={2} maxLength={demoMode ? 400 : 1000} disabled={busy} onChange={event => setGoal(event.target.value)} rows={3} required /></label>
        <div className="studyGoal"><p>{demoMode ? "计划最多 4 轮模型调用、2 次检索；自测反馈最多 2 轮、1 次检索。每次估算费用预留上限 $0.05、全局每天 $0.10、每月 $0.50（UTC）。生成计划和提交自测共用每个网络地址每天 2 次额度。" : "使用当前课程的自建混合索引。单次估算费用预留上限 $0.05、全局每天 $0.50（UTC）。"}生成会调用模型，确认和勾选任务不调用模型。</p>
          <button disabled={busy || !course || !ready}>{busy ? "正在执行，请稍候…" : pending.current ? "重试连接（复用请求）" : "生成新草稿（调用模型）"}</button></div>
      </form>
      <RequestStatus active={busy} agent />
      {error && <div className="error" role="alert">{error}<p>先点击「刷新已保存的运行」（不调用模型）。不要直接新建相同任务。</p></div>}
      <div className="studyLayout"><aside className="studyHistory"><h3>已保存的运行</h3>
        <button disabled={busy || !course} onClick={async () => {
          setError(""); setBusy(true);
          try { const data = await call<StudyPlan[]>(`?course=${encodeURIComponent(course)}`); setPlans(data); setSelectedId(current => data.some(item => item.id === current) ? current : data[0]?.id ?? ""); setReady(true); }
          catch { setError("无法刷新计划，请检查后端连接。"); }
          finally { setBusy(false); }
        }}>刷新已保存的运行</button>
        {plans.length === 0 && <p>{demoMode ? "暂无计划。连接成功后即可生成草稿。" : "暂无计划。请先在资料管理中建立混合索引。"}</p>}
        {plans.map(plan => <button className={`historyItem ${selectedId === plan.id ? "active" : ""}`} key={plan.id} disabled={busy} onClick={() => setSelectedId(plan.id)}>
          <strong>{plan.title ?? plan.request.goal}</strong><small>{statusLabel[plan.status]} · {plan.tasks.filter(task => task.done).length}/{plan.tasks.length}</small>
        </button>)}
      </aside><div className="studyDetail">
        {!selected && <div className="notice">计划、来源引用、工具执行步骤和任务进度将在这里显示。刷新页面后仍可读取。</div>}
        {selected && <>
          <div className="studySummary"><h3>{selected.title ?? "Agent 运行"}</h3><p>{statusLabel[selected.status]} · 计划生成估算 / 预留 ${selected.accounted_cost_usd.toFixed(6)} · 未结算调用 {selected.uncertain_calls}</p>
            {(selected.feedback?.length ?? 0) > 0 && <p>自测反馈合计估算 / 预留 ${(selected.feedback ?? []).reduce((sum, item) => sum + item.accounted_cost_usd, 0).toFixed(6)} · 未结算调用 {(selected.feedback ?? []).reduce((sum, item) => sum + item.uncertain_calls, 0)}</p>}
            {selected.status === "draft" && <><p>请先检查学习任务和引用。引用原文匹配不等于所有结论都正确，仍需你审核。</p>
              <button disabled={busy} onClick={() => mutate(`/${selected.id}/confirm`, "POST", { confirmed: true, version: selected.version })}>确认并启用这份计划</button></>}
            {selected.status === "running" && <p>运行尚未完成。请稍后刷新；不要重复创建。若后端曾中断，此记录不会自动恢复，可明确生成新草稿。</p>}
            {selected.status === "failed" && <div className="error">本次未生成有效计划（{selected.error_code}）。没有启用任何任务。请检查索引、API 配置或费用限制；再次生成将使用新请求并可能计费。</div>}
          </div>
          <details className="studyTrace"><summary>查看工具执行记录（{selected.trace.length} 步）</summary><ol>{selected.trace.map(step => <li key={step.step}>{step.tool} · {step.status}{step.evidence_count !== undefined ? ` · ${step.evidence_count} 条证据` : ""}</li>)}</ol></details>
          {selected.tasks.map(task => <article className={`studyTask ${task.done ? "done" : ""}`} key={task.id}>
            <h3>第 {task.day} 天 · {task.title}</h3><p>{task.minutes} 分钟 · {task.study_goal}</p>
            <label className="taskToggle"><input type="checkbox" checked={task.done} disabled={busy || selected.status !== "active"} onChange={event => mutate(`/${selected.id}/tasks/${task.id}`, "PATCH", { done: event.target.checked, version: selected.version })} />{task.done ? "已完成" : "标记完成（需先确认计划）"}</label>
            <h4>自测题</h4><p>{task.quiz_question}</p><details><summary>查看参考答案</summary><p>{task.quiz_answer}</p></details>
            {task.citations.map((citation, index) => <div className="studyCitation" key={index}><button type="button" disabled={!citation.chunk_id} onClick={() => onSource(citation)}>[{citation.source_id}] {citation.filename}{citation.page ? ` · ${citation.locator_type === "slide" ? "幻灯片" : "位置"} ${citation.page}` : ""} · 查看原文</button><blockquote>{citation.quote}</blockquote></div>)}
            <TaskFeedback key={`${selected.id}-${task.id}`} plan={selected} task={task} apiUrl={apiUrl} demoMode={demoMode} busy={busy} onPlan={receive} onBusy={setBusy} onError={setError} onSource={onSource} />
          </article>)}
        </>}
      </div></div>
    </>
  </section>;
}
