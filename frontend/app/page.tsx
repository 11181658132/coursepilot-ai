"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import StudyAgent from "./study-agent";

type Course = { course: string; lecture_count: number; tutorial_count: number; other_count: number; indexed_count: number };
type Source = { chunk_id?: string; path?: string; page?: number; locator_type?: "page" | "slide" | "section"; week?: number; source_id?: string };
type Citation = Source & { filename: string; file_id?: string; quote?: string };
type Evidence = Source & { filename: string; score?: number; text: string; bm25_score?: number; vector_score?: number; rerank_score?: number };
type SourcePreview = Source & { filename: string; text: string };
type Conversation = { id: string; title: string; course: string; material_type: string; message_count: number; updated_at: string };
type Message = {
  id: string; conversation_id: string; role: "user" | "assistant"; content: string;
  response_id?: string; request_id?: string; citations: Citation[]; evidence: Evidence[]; latency_ms: number;
  estimated_cost_usd: number; feedback?: "helpful" | "unhelpful";
};
type Answer = {
  answer: string; response_id?: string; request_id?: string; conversation_id?: string; message_id: string;
  citations?: Citation[]; evidence?: Evidence[]; latency_ms?: number;
  estimated_cost_usd?: number; remaining_today?: number;
};
type DocumentRecord = {
  path: string; filename: string; course: string; material_type: "lecture" | "tutorial" | "other";
  size_bytes: number; status: "indexed" | "pending" | "missing";
};
type SyncReport = {
  uploaded: number; replaced: number; skipped: number; failed: number;
  errors: string[];
  removed?: number; chunk_count?: number; estimated_cost_usd?: number;
};
type IndexJob = {
  id: string; status: "queued" | "running" | "succeeded" | "failed"; phase: string;
  processed: number; total: number; attempts: number; max_attempts: number;
  error_code?: string; report?: SyncReport; estimated_cost_usd: number; available_at: number;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

const DEMO_COURSES: Course[] = [
  { course: "DEMO101", lecture_count: 3, tutorial_count: 2, other_count: 0, indexed_count: 5 },
];
const DEMO_CONVERSATIONS: Conversation[] = [];
const DEMO_MESSAGES: Message[] = [];
const DEMO_DOCUMENTS: DocumentRecord[] = [
  { path: "DEMO101/lecture/lecture-01-rag-foundations.md", filename: "lecture-01-rag-foundations.md", course: "DEMO101", material_type: "lecture", size_bytes: 1486, status: "indexed" },
  { path: "DEMO101/lecture/lecture-02-retrieval-and-citations.md", filename: "lecture-02-retrieval-and-citations.md", course: "DEMO101", material_type: "lecture", size_bytes: 1488, status: "indexed" },
  { path: "DEMO101/lecture/lecture-03-safety-and-grounding.md", filename: "lecture-03-safety-and-grounding.md", course: "DEMO101", material_type: "lecture", size_bytes: 1457, status: "indexed" },
  { path: "DEMO101/tutorial/tutorial-01-evaluation-playbook.md", filename: "tutorial-01-evaluation-playbook.md", course: "DEMO101", material_type: "tutorial", size_bytes: 1475, status: "indexed" },
  { path: "DEMO101/tutorial/tutorial-02-cost-and-observability.md", filename: "tutorial-02-cost-and-observability.md", course: "DEMO101", material_type: "tutorial", size_bytes: 1482, status: "indexed" },
];
const DEMO_SUGGESTIONS = [
  "RAG 和微调有什么区别？",
  "为什么引用来源很重要？",
  "如何评估一个 RAG 系统？",
  "公开 AI Demo 应该怎样控制费用？",
];

export default function Home() {
  const [view, setView] = useState<"chat" | "documents" | "agent">("chat");
  const [agentGoal, setAgentGoal] = useState("");
  const [courses, setCourses] = useState<Course[]>(DEMO_MODE ? DEMO_COURSES : []);
  const [documents, setDocuments] = useState<DocumentRecord[]>(DEMO_MODE ? DEMO_DOCUMENTS : []);
  const [conversations, setConversations] = useState<Conversation[]>(DEMO_MODE ? DEMO_CONVERSATIONS : []);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>(DEMO_MODE ? DEMO_MESSAGES : []);
  const [course, setCourse] = useState(DEMO_MODE ? DEMO_COURSES[0].course : "");
  const [materialType, setMaterialType] = useState(DEMO_MODE ? "lecture" : "all");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [documentLoading, setDocumentLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [uploadCourse, setUploadCourse] = useState(DEMO_MODE ? DEMO_COURSES[0].course : "");
  const [uploadMaterial, setUploadMaterial] = useState("lecture");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [syncPreview, setSyncPreview] = useState<SyncReport | null>(null);
  const [demoThreads, setDemoThreads] = useState<Record<string, Message[]>>({});
  const [remainingToday, setRemainingToday] = useState(5);
  const [engine, setEngine] = useState<"file_search" | "hybrid">("hybrid");
  const [indexJob, setIndexJob] = useState<IndexJob | null>(null);
  const lastJobState = useRef("");
  const [sourcePreview, setSourcePreview] = useState<SourcePreview | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);

  async function loadConversations() {
    if (DEMO_MODE) {
      setConversations(DEMO_CONVERSATIONS);
      return;
    }
    const response = await fetch(`${API_URL}/api/conversations`);
    if (!response.ok) throw new Error("无法读取聊天记录");
    setConversations(await response.json());
  }

  useEffect(() => {
    if (DEMO_MODE) {
      setCourses(DEMO_COURSES);
      setConversations(DEMO_CONVERSATIONS);
      setCourse(DEMO_COURSES[0].course);
      setUploadCourse(DEMO_COURSES[0].course);
      setActiveConversationId(null);
      setMessages(DEMO_MESSAGES);
      return;
    }
    Promise.all([
      fetch(`${API_URL}/api/courses?engine=${engine}`).then((response) => {
        if (!response.ok) throw new Error("无法读取课程列表");
        return response.json() as Promise<Course[]>;
      }),
      fetch(`${API_URL}/api/conversations`).then((response) => {
        if (!response.ok) throw new Error("无法读取聊天记录");
        return response.json() as Promise<Conversation[]>;
      }),
    ])
      .then(([courseData, conversationData]) => {
        setCourses(courseData);
        setConversations(conversationData);
        if (courseData.length > 0) {
          setCourse(courseData[0].course);
          setUploadCourse(courseData[0].course);
        }
      })
      .catch((reason: Error) => setError(reason.message));
  }, [engine]);

  async function refreshLibrary() {
    if (DEMO_MODE) {
      setCourses(DEMO_COURSES);
      setDocuments(DEMO_DOCUMENTS);
      return;
    }
    const [courseResponse, documentResponse] = await Promise.all([
      fetch(`${API_URL}/api/courses?engine=${engine}`),
      fetch(`${API_URL}/api/documents?engine=${engine}`),
    ]);
    if (!courseResponse.ok || !documentResponse.ok) throw new Error("无法读取资料列表");
    const courseData = await courseResponse.json() as Course[];
    setCourses(courseData);
    setDocuments(await documentResponse.json());
    if (!uploadCourse && courseData.length > 0) setUploadCourse(courseData[0].course);
  }

  useEffect(() => {
    if (view !== "documents") return;
    refreshLibrary().catch((reason: Error) => setError(reason.message));
  }, [view, engine]);

  useEffect(() => {
    if (DEMO_MODE || view !== "documents" || engine !== "hybrid") return;
    const controller = new AbortController();
    let fetching = false;
    async function poll() {
      if (fetching) return;
      fetching = true;
      try {
        const response = await fetch(`${API_URL}/api/index-jobs`, { signal: controller.signal });
        if (!response.ok) throw new Error("无法读取索引任务；请检查后端连接。");
        const jobs = await response.json() as IndexJob[];
        if (controller.signal.aborted) return;
        const latest = jobs[0] ?? null;
        setIndexJob(latest);
        const key = latest ? `${latest.id}:${latest.status}` : "";
        if (latest && key !== lastJobState.current && ["succeeded", "failed"].includes(latest.status)) {
          await refreshLibrary();
        }
        lastJobState.current = key;
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "任务查询失败");
      } finally { fetching = false; }
    }
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [view, engine]);

  const selected = useMemo(
    () => courses.find((item) => item.course === course),
    [courses, course],
  );

  async function openConversation(item: Conversation) {
    setError("");
    if (DEMO_MODE) {
      setMessages(demoThreads[item.id] ?? []);
      setActiveConversationId(item.id);
      setCourse(item.course);
      setMaterialType(item.material_type);
      return;
    }
    try {
      const response = await fetch(`${API_URL}/api/conversations/${item.id}/messages`);
      if (!response.ok) throw new Error("无法读取对话内容");
      setMessages(await response.json());
      setActiveConversationId(item.id);
      setCourse(item.course);
      setMaterialType(item.material_type);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    }
  }

  function newConversation() {
    setActiveConversationId(null);
    setMessages([]);
    setQuestion("");
    setError("");
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    const submittedQuestion = question.trim();
    if (!submittedQuestion || !course) return;
    setLoading(true);
    setError("");
    if (DEMO_MODE) {
      const conversationId = activeConversationId ?? `demo-${Date.now()}`;
      const userMessage: Message = {
        id: `demo-user-${Date.now()}`, conversation_id: conversationId, role: "user",
        content: submittedQuestion, citations: [], evidence: [], latency_ms: 0,
        estimated_cost_usd: 0,
      };
      const pendingMessages = [...messages, userMessage];
      setMessages(pendingMessages);
      try {
        const response = await fetch("/api/demo/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            course,
            engine,
            material_type: materialType,
            question: submittedQuestion,
            history: messages.map((message) => ({ role: message.role, content: message.content })),
            website: "",
          }),
        });
        const data = await response.json() as Answer & { detail?: string };
        if (!response.ok) throw new Error(data.detail ?? "提问失败");
        const assistantMessage: Message = {
          id: data.message_id,
          conversation_id: conversationId,
          role: "assistant",
          content: data.answer,
          response_id: data.response_id,
          request_id: data.request_id,
          citations: data.citations ?? [],
          evidence: data.evidence ?? [],
          latency_ms: data.latency_ms ?? 0,
          estimated_cost_usd: data.estimated_cost_usd ?? 0,
        };
        const completedMessages = [...pendingMessages, assistantMessage];
        setMessages(completedMessages);
        setDemoThreads((current) => ({ ...current, [conversationId]: completedMessages }));
        setConversations((current) => {
          const existing = current.find((item) => item.id === conversationId);
          const updated: Conversation = {
            id: conversationId,
            title: existing?.title ?? submittedQuestion.slice(0, 36),
            course,
            material_type: materialType,
            message_count: completedMessages.length,
            updated_at: new Date().toISOString(),
          };
          return [updated, ...current.filter((item) => item.id !== conversationId)];
        });
        setActiveConversationId(conversationId);
        setRemainingToday(data.remaining_today ?? remainingToday);
        setQuestion("");
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "发生未知错误");
      } finally {
        setLoading(false);
      }
      return;
    }
    try {
      const previousResponseId = [...messages]
        .reverse()
        .find((message) => message.role === "assistant")?.response_id;
      const response = await fetch(`${API_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          course, material_type: materialType, question: submittedQuestion,
          conversation_id: activeConversationId, previous_response_id: previousResponseId, engine,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "提问失败");
      const answer = data as Answer;
      if (!answer.conversation_id) throw new Error("回答缺少对话编号");
      setQuestion("");
      setActiveConversationId(answer.conversation_id);
      const messagesResponse = await fetch(
        `${API_URL}/api/conversations/${answer.conversation_id}/messages`,
      );
      if (!messagesResponse.ok) throw new Error("回答成功，但刷新聊天记录失败");
      setMessages(await messagesResponse.json());
      await loadConversations();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    } finally {
      setLoading(false);
    }
  }

  async function rate(messageId: string, rating: "helpful" | "unhelpful") {
    setError("");
    if (DEMO_MODE) {
      setMessages((current) => {
        const updated = current.map((message) =>
          message.id === messageId ? { ...message, feedback: rating } : message,
        );
        if (activeConversationId) {
          setDemoThreads((threads) => ({ ...threads, [activeConversationId]: updated }));
        }
        return updated;
      });
      return;
    }
    try {
      const response = await fetch(`${API_URL}/api/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId, rating }),
      });
      if (!response.ok) throw new Error("反馈保存失败");
      setMessages((current) => current.map((message) =>
        message.id === messageId ? { ...message, feedback: rating } : message,
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    }
  }

  async function uploadDocument(event: FormEvent) {
    event.preventDefault();
    if (!uploadFile || !uploadCourse.trim()) return;
    if (DEMO_MODE) {
      setNotice("公开展示为只读模式，不会上传文件或调用外部 API。");
      return;
    }
    setDocumentLoading(true);
    setError("");
    setNotice("");
    try {
      const query = new URLSearchParams({
        course: uploadCourse.trim(), material_type: uploadMaterial, filename: uploadFile.name,
      });
      const response = await fetch(`${API_URL}/api/documents/upload?${query}`, {
        method: "POST",
        headers: { "Content-Type": "application/octet-stream" },
        body: uploadFile,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "上传失败");
      setUploadFile(null);
      setSyncPreview(null);
      setNotice("文件已安全保存。点击“检查索引更新”后再开始索引。");
      await refreshLibrary();
      const input = document.getElementById("document-file") as HTMLInputElement | null;
      if (input) input.value = "";
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    } finally {
      setDocumentLoading(false);
    }
  }

  async function previewIndex() {
    if (DEMO_MODE) {
      setSyncPreview({ uploaded: 0, replaced: 0, skipped: DEMO_DOCUMENTS.length, failed: 0, errors: [] });
      return;
    }
    setDocumentLoading(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API_URL}/api/sync/preview?engine=${engine}`, { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "检查失败");
      setSyncPreview(data);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    } finally {
      setDocumentLoading(false);
    }
  }

  async function runIndex() {
    if (DEMO_MODE) {
      setNotice("公开展示为只读模式，索引未发生变化。");
      return;
    }
    setDocumentLoading(true);
    setError("");
    setNotice("");
    try {
      if (engine === "hybrid") {
        const response = await fetch(`${API_URL}/api/index-jobs`, { method: "POST" });
        const job = await response.json() as IndexJob & { detail?: string };
        if (!response.ok) throw new Error(job.detail ?? "创建索引任务失败");
        setIndexJob(job);
        setNotice("索引任务已保存，由独立 Worker 在后台处理；刷新页面不会丢失进度。");
        setSyncPreview(null);
        return;
      }
      const response = await fetch(`${API_URL}/api/sync?engine=${engine}`, { method: "POST" });
      const data = await response.json() as SyncReport & { detail?: string };
      if (!response.ok) throw new Error(data.detail ?? "索引失败");
      setNotice(`索引完成：新增 ${data.uploaded}，替换 ${data.replaced}，跳过 ${data.skipped}，失败 ${data.failed}。`);
      setSyncPreview(null);
      await refreshLibrary();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    } finally {
      setDocumentLoading(false);
    }
  }

  async function retryIndex() {
    if (!indexJob || DEMO_MODE) return;
    setDocumentLoading(true); setError("");
    try {
      const response = await fetch(`${API_URL}/api/index-jobs/${indexJob.id}/retry`, { method: "POST" });
      const job = await response.json();
      if (!response.ok) throw new Error(job.detail ?? "重试失败");
      setIndexJob(job); setNotice("已重新排队；成功索引且未变化的文件会跳过。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "重试失败"); }
    finally { setDocumentLoading(false); }
  }

  async function deleteDocument(item: DocumentRecord) {
    if (DEMO_MODE) {
      setNotice(`公开展示为只读模式，不会删除 ${item.filename}。`);
      return;
    }
    if (!window.confirm(`确定删除“${item.filename}”吗？本地文件和对应索引都会删除。`)) return;
    setDocumentLoading(true);
    setError("");
    setNotice("");
    try {
      const response = await fetch(`${API_URL}/api/documents`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: item.path }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "删除失败");
      setNotice(`已删除 ${item.filename}`);
      await refreshLibrary();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    } finally {
      setDocumentLoading(false);
    }
  }

  function formatBytes(bytes: number) {
    if (!bytes) return "—";
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function sourceLocation(source: Source) {
    const label = source.locator_type === "slide" ? "幻灯片" : source.locator_type === "page" ? "页" : "节";
    return source.page ? ` · 第 ${source.page} ${label}${source.week ? ` · Week ${source.week}` : ""}` : "";
  }

  async function inspectSource(citation: Citation) {
    if (!citation.chunk_id) return;
    setSourceLoading(true);
    setError("");
    try {
      const response = await fetch(`${DEMO_MODE ? "/api/demo" : `${API_URL}/api`}/sources/${encodeURIComponent(citation.chunk_id)}`);
      if (!response.ok) throw new Error("原文已变化或删除，请重新建立索引。");
      setSourcePreview(await response.json());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法打开原文");
    } finally {
      setSourceLoading(false);
    }
  }

  return (
    <div className="appShell">
      <a className="skipLink" href="#workspace">跳到工作区</a>
      <aside className="appRail">
        <div className="brand"><span className="brandMark" aria-hidden="true">✦</span><span>CoursePilot<span className="brandAI">AI</span></span></div>
        <div className="railLabel">学习工作台 <span>WORKSPACE</span></div>
        <nav className="viewTabs" aria-label="页面切换">
          <button aria-current={view === "chat" ? "page" : undefined} className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}><span aria-hidden="true">◈</span>课程问答<span className="navHint">RAG</span></button>
          <button aria-current={view === "documents" ? "page" : undefined} className={view === "documents" ? "active" : ""} onClick={() => setView("documents")}><span aria-hidden="true">▤</span>资料管理<span className="navHint">KB</span></button>
          <button aria-current={view === "agent" ? "page" : undefined} className={view === "agent" ? "active" : ""} onClick={() => setView("agent")}><span aria-hidden="true">✧</span>复习计划<span className="navHint">AGENT</span></button>
        </nav>
        <div className="railNote"><span className="railNoteIcon" aria-hidden="true">↗</span><strong>不止找到答案</strong><p>从理解知识，到制定计划。<br />让每一次学习都有据可循。</p><span className="railNoteTag">RETRIEVE → LEARN → ACT</span></div>
        <div className="railFooter"><span className="avatar">CP</span><div><strong>{DEMO_MODE ? "公开演示空间" : "我的学习空间"}</strong><small>{DEMO_MODE ? "Fictional data · DEMO101" : "Personal knowledge workspace"}</small></div><span className="onlineDot" title="工作台" /></div>
      </aside>
    <main id="workspace" className="appMain">
      <div className="topbar"><div>我的工作台 <span>/</span> <strong>{view === "chat" ? "课程问答" : view === "documents" ? "资料管理" : "复习计划 Agent"}</strong></div><span className="modePill"><span className="onlineDot" />{DEMO_MODE ? "公开体验版" : "本地工作区"}</span></div>
      <header className="hero">
        <div className="heroCopy"><div className="eyebrow">YOUR KNOWLEDGE, CONNECTED</div>
        <h1>{view === "chat" ? <>把知识，<span>真正学懂。</span></> : view === "documents" ? <>让资料，<span>成为知识。</span></> : <>让目标，<span>变成行动。</span></>}</h1>
        <p>{view === "chat" ? "从课程资料中寻找证据，把问题变成清晰的理解。" : view === "documents" ? "管理学习资料，为每一次检索打好基础。" : "基于课程证据，安排每天的学习与自测。"}</p></div>
        <div className="heroArt" aria-hidden="true"><div className="orbit orbitOne" /><div className="orbit orbitTwo" /><div className="artTile artBook">▤</div><div className="artCore">✦</div><div className="artTile artCheck">✓</div><span className="artSpark">✧</span></div>
      </header>
      <div className="workspaceToolbar">
        {view !== "agent" && <label className="enginePicker">检索引擎
          <select value={engine} disabled={loading || documentLoading} onChange={(event) => {
            setEngine(event.target.value as "file_search" | "hybrid");
            newConversation(); setSyncPreview(null); setNotice(""); setSourcePreview(null);
          }}>
            <option value="file_search">File Search · 基线</option>
            <option value="hybrid">自建 RAG · 混合检索与重排序</option>
          </select>
          {engine === "hybrid" && <span className="engineHint">BM25 + 向量检索 · 来源可追溯</span>}
        </label>}
        {DEMO_MODE && <div className="demoBanner"><span aria-hidden="true">◉</span><strong>真实 AI · 虚构资料</strong><span>{view === "agent" ? "仅使用 DEMO101 示例资料，不包含真实课程。" : "不包含真实课程文件 · 每位访客每天可提问 5 次"}</span></div>}
      </div>

      {view === "agent" ? <StudyAgent apiUrl={API_URL} demoMode={DEMO_MODE} courses={courses} onSource={inspectSource} initialCourse={course} initialGoal={agentGoal} /> : view === "chat" ? <section className="workspace">
        <aside className="sidebar">
          <button className="newChat" onClick={newConversation}>＋ 新对话</button>
          <div className="sectionLabel historyLabel">聊天记录</div>
          <div className="historyList">
            {conversations.length === 0 && <p className="emptyHistory">新的灵感，从一个问题开始。</p>}
            {conversations.map((item) => (
              <button
                className={`historyItem ${activeConversationId === item.id ? "active" : ""}`}
                key={item.id}
                onClick={() => openConversation(item)}
              >
                <strong>{item.title}</strong>
                <span>{item.course} · {item.message_count} 条消息</span>
              </button>
            ))}
          </div>

          <div className="sectionLabel scopeLabel">知识范围</div>
          <label>课程
            <select value={course} disabled={Boolean(activeConversationId)} onChange={(event) => setCourse(event.target.value)}>
              {courses.map((item) => <option key={item.course} value={item.course}>{item.course}</option>)}
            </select>
          </label>
          <label>资料类型
            <select value={materialType} disabled={Boolean(activeConversationId)} onChange={(event) => setMaterialType(event.target.value)}>
              <option value="all">全部资料</option><option value="lecture">Lecture</option>
              <option value="tutorial">Tutorial</option><option value="other">其他</option>
            </select>
          </label>
          {selected && <div className="stats">
            <div><strong>{selected.lecture_count}</strong><span>Lecture</span></div>
            <div><strong>{selected.tutorial_count}</strong><span>Tutorial</span></div>
            <div><strong>{selected.indexed_count}</strong><span>已索引</span></div>
          </div>}
          <div className="privacy">{DEMO_MODE
            ? `问题只用于生成当前回答，不写入项目数据库。今天还可提问 ${remainingToday} 次。`
            : "聊天和反馈仅保存在本机，不会进入代码仓库。"}</div>
        </aside>

        <div className="content">
          {messages.map((message, messageIndex) => message.role === "user" ? (
            <article className="userMessage" key={message.id}><span>你</span><p>{message.content}</p></article>
          ) : (
            <article className="answerCard" key={message.id}>
              <div className="answerHeader"><div className="sectionLabel">回答</div><span>{(message.latency_ms / 1000).toFixed(1)} 秒</span></div>
              <div className="answer"><ReactMarkdown>{message.content}</ReactMarkdown></div>
              <div className="sources"><h2>引用来源</h2>
                {message.citations.length === 0 ? <p>本次回答没有返回文件引用，请谨慎使用。</p> : <ul>
                  {message.citations.map((citation) => <li key={citation.chunk_id ?? `${citation.file_id}-${citation.filename}`}>
                    {citation.chunk_id ? <button className="sourceLink" disabled={sourceLoading} onClick={() => inspectSource(citation)}>
                      [{citation.source_id}] {citation.filename}{sourceLocation(citation)} · 查看原文
                    </button> : citation.filename}
                    {citation.quote && <blockquote>{citation.quote}</blockquote>}
                  </li>)}
                </ul>}
              </div>
              <button type="button" disabled={loading || message.citations.length === 0} onClick={() => {
                const prompt = messages.slice(0, messageIndex).reverse().find(item => item.role === "user")?.content ?? "";
                setAgentGoal(("围绕以下学习问题，结合课程资料安排复习和自测：" + prompt).slice(0, DEMO_MODE ? 400 : 1000));
                setView("agent");
              }}>基于这个问题制定复习计划</button>
              {message.evidence.length > 0 && <details><summary>查看检索证据</summary>
                {message.evidence.map((item, index) => <div className="evidence" key={`${item.filename}-${index}`}>
                  <strong>{item.filename}{sourceLocation(item)}</strong>{item.rerank_score != null ? <span>重排序评分 {item.rerank_score}/3</span> : item.score != null && <span>检索得分 {item.score.toFixed(3)}</span>}<p>{item.text}</p>
                </div>)}
              </details>}
              <div className="feedbackRow"><span>这个回答有帮助吗？</span>
                <button className={message.feedback === "helpful" ? "selected" : ""} onClick={() => rate(message.id, "helpful")} aria-label="有帮助">👍</button>
                <button className={message.feedback === "unhelpful" ? "selected" : ""} onClick={() => rate(message.id, "unhelpful")} aria-label="没帮助">👎</button>
                <span className="cost">估算 ${message.estimated_cost_usd.toFixed(6)}</span>
                {message.request_id && <span className="requestId" title={message.request_id}>请求 {message.request_id.slice(0, 8)}</span>}
              </div>
            </article>
          ))}

          <form onSubmit={ask} className="askCard">
            {!activeConversationId && <div className="askIntro"><span className="assistantMark" aria-hidden="true">✦</span><div className="sectionLabel">ASK YOUR KNOWLEDGE</div><p>不用翻遍所有课件，<br /><strong>从你想知道的开始。</strong></p></div>}
            <label htmlFor="question">{activeConversationId ? "继续追问" : "你想理解什么？"}</label>
            {DEMO_MODE && <div className="suggestions" aria-label="示例问题">
              {DEMO_SUGGESTIONS.map((suggestion, index) => <button type="button" key={suggestion} onClick={() => setQuestion(suggestion)}><span className="suggestionNumber">0{index + 1}</span>{suggestion}<span className="suggestionArrow" aria-hidden="true">↗</span></button>)}
            </div>}
            <textarea id="question" maxLength={600} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={DEMO_MODE ? "可以询问 RAG、检索评测、安全与成本控制。" : "例如：解释 Week 3 中的核心模型，并比较它与 Week 2 方法的区别。"} rows={5} />
            <div className="formFooter"><span><span aria-hidden="true">◇</span> 基于课程证据回答 · 附带来源引用</span><button disabled={loading || !course || !question.trim()}>{loading ? "正在检索…" : "开始提问 ↗"}</button></div>
          </form>
          {error && <div className="error">{error}</div>}
        </div>
      </section> : <section className="documentWorkspace">
        <div className="managerHeader">
          <div><div className="sectionLabel">KNOWLEDGE BASE</div><h2>课程资料管理</h2>
            <p>{DEMO_MODE ? "浏览 5 份虚构示例资料。公开空间为只读，不会上传或删除文件。" : "上传只会保存到本地知识库；检查后由你决定是否发送到 OpenAI 建立索引。"}</p>
          </div>
          <button className="secondaryButton" onClick={previewIndex} disabled={documentLoading}>检查索引更新</button>
        </div>

        <form className="uploadCard" onSubmit={uploadDocument}>
          <label>课程
            <input list="course-options" value={uploadCourse} onChange={(event) => setUploadCourse(event.target.value)} placeholder="例如 90016" required />
            <datalist id="course-options">{courses.map((item) => <option key={item.course} value={item.course} />)}</datalist>
          </label>
          <label>资料类型
            <select value={uploadMaterial} onChange={(event) => setUploadMaterial(event.target.value)}>
              <option value="lecture">Lecture</option><option value="tutorial">Tutorial</option><option value="other">其他</option>
            </select>
          </label>
          <label className="fileField">选择文件
            <input id="document-file" type="file" accept=".pdf,.pptx,.txt,.md,.docx" onChange={(event) => setUploadFile(event.target.files?.[0] ?? null)} required />
          </label>
          <button disabled={documentLoading || !uploadFile || !uploadCourse.trim()}>{documentLoading ? "处理中…" : "上传到本地"}</button>
        </form>

        {syncPreview && <div className="syncCard">
          <div><strong>索引预览</strong><span>新增 {syncPreview.uploaded} · 替换 {syncPreview.replaced} · 已是最新 {syncPreview.skipped} · 移除缺失索引 {syncPreview.removed ?? 0}</span></div>
          <button onClick={runIndex} disabled={documentLoading || (syncPreview.uploaded + syncPreview.replaced + (syncPreview.removed ?? 0) === 0)}>
            {documentLoading ? "正在索引…" : "开始索引"}
          </button>
        </div>}
        {!DEMO_MODE && engine === "hybrid" && indexJob && <section className="syncCard" aria-label="后台索引任务" aria-live="polite">
          <div><strong>后台索引 · {({ queued: "等待处理", running: "处理中", succeeded: "已完成", failed: "失败" })[indexJob.status]}</strong>
            <span>阶段：{({ waiting: "等待 Worker", scanning: "扫描", parsing: "解析", embedding: "向量化", committing: "保存索引", finalizing: "清理", interrupted: "中断恢复", retry_wait: "等待限额恢复", succeeded: "完成", failed: "失败" } as Record<string, string>)[indexJob.phase] ?? indexJob.phase} · 已处理 {indexJob.processed}/{indexJob.total} · 尝试 {indexJob.attempts}/{indexJob.max_attempts}</span>
            <small>任务 {indexJob.id} · 已记录估算费用 ${indexJob.estimated_cost_usd.toFixed(6)}</small>
            {indexJob.status === "queued" && <p>{indexJob.phase === "retry_wait" ? `下次尝试不早于 ${new Date(indexJob.available_at * 1000).toLocaleString()}` : "请保持独立索引 Worker 运行；任务已持久化，无需保持此页面打开。"}</p>}
            {indexJob.error_code && <p>错误：{({ provider_quota_exhausted: "API 余额或配额不足，请处理账单后手动重试。", provider_authentication: "API 密钥无效或权限不足，请检查配置。", configuration_missing: "未配置 API Key，请在本地 .env 中配置后重试。", indexing_failed: "部分文件未能索引，请查看下方错误。", worker_interrupted: "上次 Worker 意外中断。" } as Record<string, string>)[indexJob.error_code] ?? indexJob.error_code}</p>}
            {indexJob.report && <p>新增 {indexJob.report.uploaded} · 替换 {indexJob.report.replaced} · 跳过 {indexJob.report.skipped} · 失败 {indexJob.report.failed}</p>}
            {indexJob.report?.errors.map((message, position) => <p key={position}>{message}</p>)}
          </div>
          {indexJob.status === "failed" && indexJob.attempts < indexJob.max_attempts && <button disabled={documentLoading} onClick={retryIndex}>重试失败任务</button>}
        </section>}
        {notice && <div className="notice">{notice}</div>}
        {error && <div className="error">{error}</div>}

        <div className="documentTableWrap">
          <table className="documentTable"><thead><tr><th>文件</th><th>课程</th><th>类型</th><th>大小</th><th>状态</th><th></th></tr></thead>
            <tbody>{documents.map((item) => <tr key={item.path}>
              <td><strong>{item.filename}</strong><small>{item.path}</small></td>
              <td>{item.course}</td><td>{item.material_type}</td><td>{formatBytes(item.size_bytes)}</td>
              <td><span className={`status ${item.status}`}>{item.status === "indexed" ? "已索引" : item.status === "pending" ? "待索引" : "本地缺失"}</span></td>
              <td><button className="deleteButton" disabled={documentLoading || DEMO_MODE} onClick={() => deleteDocument(item)}>{DEMO_MODE ? "只读" : "删除"}</button></td>
            </tr>)}</tbody>
          </table>
          {documents.length === 0 && <p className="emptyDocuments">知识库里还没有资料。</p>}
        </div>
      </section>}
      {view === "agent" && error && <div className="error" role="alert">{error}</div>}
      {sourcePreview && <div className="sourceOverlay" onClick={() => setSourcePreview(null)}>
        <section className="sourceDialog" role="dialog" aria-modal="true" aria-label="引用原文" tabIndex={-1}
          onClick={(event) => event.stopPropagation()} onKeyDown={(event) => {
            if (event.key === "Escape") setSourcePreview(null);
            if (event.key === "Tab") {
              const controls = event.currentTarget.querySelectorAll<HTMLElement>("button, a");
              const first = controls[0]; const last = controls[controls.length - 1];
              if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
              else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
            }
          }}>
          <button className="secondaryButton" autoFocus onClick={() => setSourcePreview(null)}>关闭原文</button>
          <h2>{sourcePreview.filename}</h2><p>{sourceLocation(sourcePreview)}</p>
          <pre>{sourcePreview.text}</pre>
          <a href={`${DEMO_MODE ? "/api/demo" : `${API_URL}/api`}/sources/${sourcePreview.chunk_id}/file${sourcePreview.locator_type === "page" ? `#page=${sourcePreview.page}` : ""}`} target="_blank" rel="noreferrer">{sourcePreview.locator_type === "page" ? "打开 PDF 对应页" : "下载源文件"}</a>
        </section>
      </div>}
      <footer className="workspaceFooter"><span>CoursePilot AI</span><span>有据可循的回答，有迹可循的学习。</span><span>RAG + AGENT</span></footer>
    </main>
    </div>
  );
}
