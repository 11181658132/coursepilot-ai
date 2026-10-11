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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export default function Home() {
  const [courses, setCourses] = useState<Course[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [course, setCourse] = useState("");
  const [materialType, setMaterialType] = useState("all");
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function loadConversations() {
    const response = await fetch(`${API_URL}/api/conversations`);
    if (!response.ok) throw new Error("无法读取聊天记录");
    setConversations(await response.json());
  }

  useEffect(() => {
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
        if (courseData.length > 0) setCourse(courseData[0].course);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  const selected = useMemo(
    () => courses.find((item) => item.course === course),
    [courses, course],
  );

  async function openConversation(item: Conversation) {
    setError("");
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

  return (
    <main>
      <header className="hero">
        <div className="eyebrow">GROUNDED COURSE INTELLIGENCE</div>
        <h1>CoursePilot <span>AI</span></h1>
        <p>从 Lecture 和 Tutorial 中检索证据，再回答你的问题。</p>
      </header>

      <section className="workspace">
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
      </section>
    </main>
  );
}
