"use client";

import { useEffect, useState } from "react";

export default function RequestStatus({ active, agent = false }: { active: boolean; agent?: boolean }) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) return;
    const started = Date.now();
    setSeconds(0);
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [active]);
  if (!active) return null;
  return <div className="requestStatus" role="status" aria-live="polite">
    <strong>{agent ? "正在等待 Agent 操作结果" : "正在等待检索与回答结果"}</strong>
    <span aria-hidden="true">已等待 {seconds} 秒</span>
    <p>{agent ? "生成类操作可能包含检索与模型调用，确认或更新进度只写入状态。" : "服务器返回后才会显示回答与引用；当前不展示未经确认的分步进度。"}请勿重复提交。{seconds >= 30 ? "等待较久不代表失败；如果连接中断，请先检查已有结果。" : ""}</p>
  </div>;
}
