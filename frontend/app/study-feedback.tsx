"use client";

import { FormEvent, useRef, useState } from "react";
import { StudyCitation, StudyPlan, StudyTask, studyRequest } from "../lib/study-api";

type Props = { plan: StudyPlan; task: StudyTask; apiUrl: string; demoMode: boolean; busy: boolean;
  onPlan: (plan: StudyPlan) => void; onBusy: (busy: boolean) => void; onError: (error: string) => void;
  onSource: (citation: StudyCitation) => void };

export default function TaskFeedback({ plan, task, apiUrl, demoMode, busy, onPlan, onBusy, onError, onSource }: Props) {
  const [answer, setAnswer] = useState("");
  const [prepared, setPrepared] = useState(false);
  const pending = useRef<{ key: string; id: string; version: number } | null>(null);
  const entries = (plan.feedback ?? []).filter(item => item.task_id === task.id);
  async function submit(event: FormEvent) {
    event.preventDefault(); onBusy(true); onError(""); setPrepared(false);
    const key = JSON.stringify({ plan: plan.id, task: task.id, answer: answer.trim() });
    if (pending.current?.key !== key) pending.current = { key, id: crypto.randomUUID(), version: plan.version };
    try {
      const updated = await studyRequest<StudyPlan>(apiUrl, `/${plan.id}/tasks/${task.id}/answers`, {
        method: "POST", body: JSON.stringify({ request_id: pending.current.id, version: pending.current.version, answer: answer.trim() }),
      }, demoMode);
      onPlan(updated); pending.current = null;
    } catch (reason) { onError(reason instanceof Error ? reason.message : "反馈连接失败；不会自动重试。"); }
    finally { onBusy(false); }
  }
  async function confirm(feedbackId: string) {
    onBusy(true); onError("");
    try { onPlan(await studyRequest<StudyPlan>(apiUrl, `/${plan.id}/feedbacks/${feedbackId}/confirm`, {
      method: "POST", body: JSON.stringify({ confirmed: true, version: plan.version }),
    }, demoMode)); }
    catch (reason) { onError(reason instanceof Error ? reason.message : "应用调整失败，请刷新。"); }
    finally { onBusy(false); }
  }
  return <section className="quizFeedback" aria-label={`第 ${task.day} 天自测反馈`}>
    <h4>写下你的理解</h4>
    <form onSubmit={submit}>
      <label>第 {task.day} 天自测答案<textarea value={answer} onChange={event => setAnswer(event.target.value)}
        minLength={2} maxLength={demoMode ? 400 : 1000} rows={3} required disabled={busy || plan.status !== "active"}
        placeholder="用自己的话回答。不要输入真实个人信息。" /></label>
      <p className="feedbackDisclaimer">AI 形成性反馈，不是老师评分或标准答案。可能判断错误，请结合课程原文核查。提交会调用模型，确认调整不会调用模型。</p>
      {prepared && <p className="notice">已填回原答案，尚未重新提交。再次提交会创建新请求，可能计费。</p>}
      <button disabled={busy || plan.status !== "active" || answer.trim().length < 2}>
        {busy ? "处理中…" : pending.current ? "重试反馈连接（复用请求）" : "提交答案，获取证据反馈"}</button>
    </form>
    {entries.map(entry => <div className="feedbackEntry" key={entry.id}>
      <details><summary>查看本次提交的答案</summary><p>{entry.request.answer}</p></details>
      <p className="feedbackCost">本次估算 / 预留 ${entry.accounted_cost_usd.toFixed(6)} · 未结算调用 {entry.uncertain_calls}</p>
      {!!entry.trace?.length && <details><summary>查看本次反馈工具记录</summary><ol>{entry.trace.map(step => <li key={step.step}>{step.tool} · {step.status}{step.evidence_count !== undefined ? ` · ${step.evidence_count} 条证据` : ""}</li>)}</ol></details>}
      {entry.status === "running" && <p role="status">反馈生成中，请刷新查看；不要重复创建。</p>}
      {entry.recovery_available && <p className="notice">本次反馈检查点已过期。请在计划顶部核对并结束过期运行；原计划不会自动改变。</p>}
      {entry.status === "interrupted" && <div className="notice"><p>反馈已中断，未修改原计划。已完成步骤和未结算费用预留保留，不会自动重试。</p><button type="button" disabled={busy || plan.status !== "active"} onClick={() => {
        pending.current = null; setAnswer(entry.request.answer); setPrepared(true);
      }}>填回本次答案（不调用模型）</button></div>}
      {entry.status === "failed" && <p className="error">本次反馈未完成（{entry.error_code}），未修改原计划。再次提交可能计费。</p>}
      {entry.assessment && <>
        <strong className={`verdict ${entry.assessment.verdict}`}>{({ mostly_correct: "AI 建议：基本理解", needs_review: "AI 建议：值得复习", uncertain: "暂时无法可靠判断" })[entry.assessment.verdict]}</strong>
        <p>{entry.assessment.explanation}</p>
        {entry.assessment.weaknesses.length > 0 && <div><h4>可能的薄弱点 · 需要你核对</h4><ul>{entry.assessment.weaknesses.map((topic, index) => <li key={index}>{topic}</li>)}</ul></div>}
        {entry.assessment.citations.map((citation, index) => <div className="studyCitation" key={index}>
          <button type="button" disabled={!citation.chunk_id} onClick={() => onSource(citation)}>[{citation.source_id}] {citation.filename} · 核对依据</button><blockquote>{citation.quote}</blockquote>
        </div>)}
      </>}
      {entry.adjustments.length > 0 && <section className="adjustmentProposal" aria-label="剩余计划调整草案">
        <h4>{entry.status === "active" ? "已确认的计划调整" : "建议调整剩余计划 · 待你确认"}</h4>
        {entry.adjustments.map(edit => {
          const previous = (entry.remaining_tasks ?? plan.tasks).find(item => item.id === edit.task_id);
          return <article key={edit.task_id}>
            <strong>第 {previous?.day} 天：{previous?.title} → {edit.title}</strong><p>{edit.study_goal}</p>
            <details><summary>查看新自测题、参考答案与来源</summary><p>{edit.quiz_question}</p><p>{edit.quiz_answer}</p>{edit.citations.map((citation, index) => <div className="studyCitation" key={index}><button type="button" disabled={!citation.chunk_id} onClick={() => onSource(citation)}>{citation.filename} · 查看原文</button><blockquote>{citation.quote}</blockquote></div>)}</details>
          </article>;
        })}
        {entry.status === "active" ? <p className="notice">你已确认，这份调整已保存。</p> : entry.base_version !== plan.version ? <p className="notice">计划或进度已变化，这份旧草案不可应用。需要时重新提交答案获取建议。</p> : <>
          <p>不确认就保留原计划。只改未完成的后续任务，不改每天时长、日期顺序或完成状态。</p>
          <button type="button" disabled={busy || entry.status !== "draft"} onClick={() => confirm(entry.id)}>确认应用这份调整（不调用模型）</button>
        </>}
      </section>}
    </div>)}
  </section>;
}
