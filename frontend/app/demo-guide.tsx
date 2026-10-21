"use client";

type Props = { disabled: boolean; onQuestion: () => void; onAgent: () => void; onDocuments: () => void };

export default function DemoGuide({ disabled, onQuestion, onAgent, onDocuments }: Props) {
  return <section className="demoGuide" aria-label="一分钟体验引导">
    <div className="guideHeading"><div><div className="sectionLabel">START HERE · DEMO101</div><h2>一分钟，认识你的学习助手。</h2></div><span className="modePill">无需上传资料</span></div>
    <p>使用 5 份虚构资料。先选一条体验路径；只填入示例，不会自动调用模型。真实生成耗时取决于模型与网络。</p>
    <div className="guidePaths">
      <article><span className="sectionLabel">01 / RAG · 有据可查</span><h3>从一个问题开始</h3><p>填入示例 → 点击提问 → 打开引用原文，核对回答依据。</p><button type="button" disabled={disabled} onClick={onQuestion}>填入示例问题 ↗</button></article>
      <article><span className="sectionLabel">02 / AGENT · 由你决定</span><h3>体验学习反馈闭环</h3><p>生成 3 天草稿 → 确认计划 → 回答第 1 天自测 → 核对反馈；若有调整建议，由你确认。</p><button type="button" disabled={disabled} onClick={onAgent}>准备示例学习目标 ↗</button></article>
    </div>
    <div className="guideFoot"><span>提问、生成计划和提交自测会调用模型并消耗体验额度；查看引用、确认和勾选进度不调用模型。</span><button type="button" disabled={disabled} onClick={onDocuments}>先看看示例资料</button></div>
    <details><summary>技术实现与演示边界</summary><p>RAG：混合检索与重排序 → 基于证据回答 → 来源核查。Agent：工具调用 → 草稿 → 人工确认 → PostgreSQL 保存计划和反馈。</p><p>引用能帮助核查，不代表结论必然正确。自测反馈不是老师评分；该闭环已通过模拟模型流程测试，真实模型反馈质量尚未完成验收。匿名计划依赖当前浏览器 Cookie；费用预留是估算，并非整站账单硬上限。</p></details>
  </section>;
}
