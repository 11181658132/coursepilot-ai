"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Course = { course: string; lecture_count: number; tutorial_count: number; other_count: number; indexed_count: number };
type Citation = { filename: string; file_id?: string };
type Evidence = { filename: string; score?: number; text: string };
type Conversation = { id: string; title: string; course: string; material_type: string; message_count: number; updated_at: string };
type Message = {
  id: string; conversation_id: string; role: "user" | "assistant"; content: string;
  response_id?: string; citations: Citation[]; evidence: Evidence[]; latency_ms: number;
  estimated_cost_usd: number; feedback?: "helpful" | "unhelpful";
};
type Answer = { answer: string; response_id?: string; conversation_id: string; message_id: string };
type DocumentRecord = {
  path: string; filename: string; course: string; material_type: "lecture" | "tutorial" | "other";
  size_bytes: number; status: "indexed" | "pending" | "missing";
};
type SyncReport = {
  uploaded: number; replaced: number; skipped: number; failed: number;
  errors: string[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const DEMO_MODE = process.env.NEXT_PUBLIC_DEMO_MODE === "true";

const DEMO_COURSES: Course[] = [
  { course: "DEMO101", lecture_count: 8, tutorial_count: 6, other_count: 0, indexed_count: 14 },
];
const DEMO_CONVERSATIONS: Conversation[] = [
  {
    id: "demo-conversation",
    title: "为什么 RAG 需要引用来源？",
    course: "DEMO101",
    material_type: "lecture",
    message_count: 2,
    updated_at: "2026-10-11T00:00:00Z",
  },
];
const DEMO_MESSAGES: Message[] = [
  {
    id: "demo-user",
    conversation_id: "demo-conversation",
    role: "user",
    content: "为什么 RAG 系统需要返回引用来源？",
    citations: [], evidence: [], latency_ms: 0, estimated_cost_usd: 0,
  },
  {
    id: "demo-assistant",
    conversation_id: "demo-conversation",
    role: "assistant",
    content: "引用让使用者能够核对回答是否真正来自指定资料，同时帮助开发者发现检索错误与幻觉。CoursePilot 会同时保留文件引用和检索证据；资料不足时，它会明确拒答，而不是补全未经证实的信息。",
    citations: [{ filename: "lecture-03-grounded-ai.md", file_id: "demo-file" }],
    evidence: [{
      filename: "lecture-03-grounded-ai.md",
      score: 0.947,
      text: "A grounded assistant should expose its evidence, distinguish retrieved facts from generated explanations, and abstain when evidence is insufficient.",
    }],
    latency_ms: 842,
    estimated_cost_usd: 0.0031,
    feedback: "helpful",
  },
];
const DEMO_DOCUMENTS: DocumentRecord[] = [
  { path: "DEMO101/lecture/lecture-03-grounded-ai.md", filename: "lecture-03-grounded-ai.md", course: "DEMO101", material_type: "lecture", size_bytes: 18432, status: "indexed" },
  { path: "DEMO101/tutorial/tutorial-03-evaluation.md", filename: "tutorial-03-evaluation.md", course: "DEMO101", material_type: "tutorial", size_bytes: 12288, status: "indexed" },
  { path: "DEMO101/lecture/lecture-04-safety.md", filename: "lecture-04-safety.md", course: "DEMO101", material_type: "lecture", size_bytes: 15360, status: "indexed" },
];

export default function Home() {
  const [view, setView] = useState<"chat" | "documents">("chat");
  const [courses, setCourses] = useState<Course[]>(DEMO_MODE ? DEMO_COURSES : []);
  const [documents, setDocuments] = useState<DocumentRecord[]>(DEMO_MODE ? DEMO_DOCUMENTS : []);
  const [conversations, setConversations] = useState<Conversation[]>(DEMO_MODE ? DEMO_CONVERSATIONS : []);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(DEMO_MODE ? DEMO_CONVERSATIONS[0].id : null);
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
      setActiveConversationId(DEMO_CONVERSATIONS[0].id);
      setMessages(DEMO_MESSAGES);
      return;
    }
    Promise.all([
      fetch(`${API_URL}/api/courses`).then((response) => {
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
  }, []);

  async function refreshLibrary() {
    if (DEMO_MODE) {
      setCourses(DEMO_COURSES);
      setDocuments(DEMO_DOCUMENTS);
      return;
    }
    const [courseResponse, documentResponse] = await Promise.all([
      fetch(`${API_URL}/api/courses`),
      fetch(`${API_URL}/api/documents`),
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
  }, [view]);

  const selected = useMemo(
    () => courses.find((item) => item.course === course),
    [courses, course],
  );

  async function openConversation(item: Conversation) {
    setError("");
    if (DEMO_MODE) {
      setMessages(DEMO_MESSAGES);
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
      const conversationId = activeConversationId ?? "demo-live-conversation";
      const userMessage: Message = {
        id: `demo-user-${Date.now()}`, conversation_id: conversationId, role: "user",
        content: submittedQuestion, citations: [], evidence: [], latency_ms: 0,
        estimated_cost_usd: 0,
      };
      const assistantMessage: Message = {
        ...DEMO_MESSAGES[1], id: `demo-assistant-${Date.now()}`,
        conversation_id: conversationId, feedback: undefined,
      };
      setMessages((current) => [...current, userMessage, assistantMessage]);
      setActiveConversationId(conversationId);
      setQuestion("");
      setLoading(false);
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
          conversation_id: activeConversationId, previous_response_id: previousResponseId,
        }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail ?? "提问失败");
      const answer = data as Answer;
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
      setMessages((current) => current.map((message) =>
        message.id === messageId ? { ...message, feedback: rating } : message,
      ));
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
      const response = await fetch(`${API_URL}/api/sync/preview`, { method: "POST" });
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
      const response = await fetch(`${API_URL}/api/sync`, { method: "POST" });
      const data = await response.json() as SyncReport & { detail?: string };
      if (!response.ok) throw new Error(data.detail ?? "索引失败");
      setNotice(`索引完成：新增 ${data.uploaded}，替换 ${data.replaced}，跳过 ${data.skipped}。`);
      setSyncPreview(null);
      await refreshLibrary();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "发生未知错误");
    } finally {
      setDocumentLoading(false);
    }
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

  return (
    <main>
      <header className="hero">
        <div className="eyebrow">GROUNDED COURSE INTELLIGENCE</div>
        <h1>CoursePilot <span>AI</span></h1>
        <p>从 Lecture 和 Tutorial 中检索证据，再回答你的问题。</p>
        <nav className="viewTabs" aria-label="页面切换">
          <button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}>课程问答</button>
          <button className={view === "documents" ? "active" : ""} onClick={() => setView("documents")}>资料管理</button>
        </nav>
        {DEMO_MODE && <div className="demoBanner"><strong>公开展示模式</strong><span>使用虚构资料和示例回答，不调用 OpenAI API，也不包含真实课程文件。</span></div>}
      </header>

      {view === "chat" ? <section className="workspace">
        <aside className="sidebar">
          <button className="newChat" onClick={newConversation}>＋ 新对话</button>
          <div className="sectionLabel historyLabel">聊天记录</div>
          <div className="historyList">
            {conversations.length === 0 && <p className="emptyHistory">还没有历史对话</p>}
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
          <div className="privacy">聊天和反馈仅保存在本机，不会进入代码仓库。</div>
        </aside>

        <div className="content">
          {messages.map((message) => message.role === "user" ? (
            <article className="userMessage" key={message.id}><span>你</span><p>{message.content}</p></article>
          ) : (
            <article className="answerCard" key={message.id}>
              <div className="answerHeader"><div className="sectionLabel">回答</div><span>{(message.latency_ms / 1000).toFixed(1)} 秒</span></div>
              <div className="answer">{message.content}</div>
              <div className="sources"><h2>引用来源</h2>
                {message.citations.length === 0 ? <p>本次回答没有返回文件引用，请谨慎使用。</p> : <ul>
                  {message.citations.map((citation) => <li key={`${citation.file_id}-${citation.filename}`}>{citation.filename}</li>)}
                </ul>}
              </div>
              {message.evidence.length > 0 && <details><summary>查看检索证据</summary>
                {message.evidence.map((item, index) => <div className="evidence" key={`${item.filename}-${index}`}>
                  <strong>{item.filename}</strong>{item.score !== undefined && <span>相关度 {item.score.toFixed(3)}</span>}<p>{item.text}</p>
                </div>)}
              </details>}
              <div className="feedbackRow"><span>这个回答有帮助吗？</span>
                <button className={message.feedback === "helpful" ? "selected" : ""} onClick={() => rate(message.id, "helpful")} aria-label="有帮助">👍</button>
                <button className={message.feedback === "unhelpful" ? "selected" : ""} onClick={() => rate(message.id, "unhelpful")} aria-label="没帮助">👎</button>
                <span className="cost">估算 ${message.estimated_cost_usd.toFixed(6)}</span>
              </div>
            </article>
          ))}

          <form onSubmit={ask} className="askCard">
            <label htmlFor="question">{activeConversationId ? "继续追问" : "你想理解什么？"}</label>
            <textarea id="question" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：解释 Week 3 中的核心模型，并比较它与 Week 2 方法的区别。" rows={5} />
            <div className="formFooter"><span>回答会附带来源文件</span><button disabled={loading || !course || !question.trim()}>{loading ? "正在检索…" : "开始提问"}</button></div>
          </form>
          {error && <div className="error">{error}</div>}
        </div>
      </section> : <section className="documentWorkspace">
        <div className="managerHeader">
          <div><div className="sectionLabel">KNOWLEDGE BASE</div><h2>课程资料管理</h2>
            <p>上传只会保存到本地知识库；检查后由你决定是否发送到 OpenAI 建立索引。</p>
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
          <div><strong>索引预览</strong><span>新增 {syncPreview.uploaded} · 替换 {syncPreview.replaced} · 已是最新 {syncPreview.skipped}</span></div>
          <button onClick={runIndex} disabled={documentLoading || (syncPreview.uploaded + syncPreview.replaced === 0)}>
            {documentLoading ? "正在索引…" : "开始索引"}
          </button>
        </div>}
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
    </main>
  );
}
