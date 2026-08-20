import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiClient, authStore, jsonBody } from "../api/client";
import type { ChatMessage, ChatSession, ChatSessionDetail, Page } from "../types";


type StreamEvent = { event: string; data: Record<string, unknown> };

const errorMessage = (error: unknown) => error instanceof ApiError ? error.message : "操作失败，请稍后重试";
const formatTime = (value: string) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "-";

async function streamChat(
  sessionId: string,
  content: string,
  signal: AbortSignal,
  onEvent: (event: StreamEvent) => void,
) {
  const response = await fetch(`/api/ai/chat/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      Authorization: `Bearer ${authStore.get()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ content }),
    signal,
  });
  if (response.status === 401) {
    authStore.clear();
    window.location.assign("/login");
    throw new ApiError(401, "登录已过期，请重新登录");
  }
  if (!response.ok) {
    let detail = "发送失败";
    try {
      detail = String((await response.json()).detail || detail);
    } catch {
      /* empty response */
    }
    throw new ApiError(response.status, detail);
  }
  if (!response.body) throw new ApiError(0, "浏览器不支持流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (!dataLines.length) continue;
      try {
        onEvent({ event, data: JSON.parse(dataLines.join("\n")) as Record<string, unknown> });
      } catch {
        /* ignore malformed event */
      }
    }
    if (done) break;
  }
}

export function AIChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [detail, setDetail] = useState<ChatSessionDetail | null>(null);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef<AbortController | null>(null);
  const messageEndRef = useRef<HTMLDivElement | null>(null);

  const loadSessions = useCallback(async (preferredId = "") => {
    const data = await apiClient<Page<ChatSession>>("/ai/chat/sessions?page=1&page_size=100");
    setSessions(data.items);
    const nextId = preferredId || detail?.session_id || data.items[0]?.session_id || "";
    if (nextId) {
      try {
        setDetail(await apiClient<ChatSessionDetail>(`/ai/chat/sessions/${nextId}`));
      } catch {
        if (data.items[0]?.session_id && data.items[0].session_id !== nextId) {
          setDetail(await apiClient<ChatSessionDetail>(`/ai/chat/sessions/${data.items[0].session_id}`));
        } else {
          setDetail(null);
        }
      }
    } else {
      setDetail(null);
    }
  }, [detail?.session_id]);

  useEffect(() => {
    apiClient<Page<ChatSession>>("/ai/chat/sessions?page=1&page_size=100").then(async (sessionPage) => {
      setSessions(sessionPage.items);
      if (sessionPage.items[0]) {
        setDetail(await apiClient<ChatSessionDetail>(`/ai/chat/sessions/${sessionPage.items[0].session_id}`));
      }
    }).catch((exc) => setError(errorMessage(exc))).finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    messageEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [detail?.messages]);

  const openSession = async (sessionId: string) => {
    setError("");
    try {
      setDetail(await apiClient<ChatSessionDetail>(`/ai/chat/sessions/${sessionId}`));
    } catch (exc) {
      setError(errorMessage(exc));
    }
  };

  const createSession = async () => {
    setBusy(true);
    setError("");
    try {
      const created = await apiClient<ChatSessionDetail>("/ai/chat/sessions", jsonBody({}));
      await loadSessions(created.session_id);
    } catch (exc) {
      setError(errorMessage(exc));
    } finally {
      setBusy(false);
    }
  };

  const removeSession = async (item: ChatSession) => {
    if (!window.confirm(`确定删除会话“${item.title}”吗？`)) return;
    setError("");
    try {
      await apiClient(`/ai/chat/sessions/${item.session_id}`, { method: "DELETE" });
      if (detail?.session_id === item.session_id) setDetail(null);
      await loadSessions();
    } catch (exc) {
      setError(errorMessage(exc));
    }
  };

  const applyStreamEvent = (event: StreamEvent) => {
    if (!detail) return;
    if (event.event === "message.started") {
      const userMessage = event.data.user_message as ChatMessage;
      const assistantMessage = event.data.assistant_message as ChatMessage;
      setDetail((current) => current ? { ...current, is_running: true, messages: [...current.messages, userMessage, assistantMessage] } : current);
      return;
    }
    const messageId = String(event.data.message_id || "");
    if (!messageId) return;
    setDetail((current) => {
      if (!current) return current;
      return {
        ...current,
        is_running: !["message.completed", "message.cancelled", "message.error"].includes(event.event),
        messages: current.messages.map((message) => {
          if (message.message_id !== messageId) return message;
          if (event.event === "message.delta") return {
            ...message,
            status: "STREAMING",
            content: "content" in event.data ? String(event.data.content || "") : message.content + String(event.data.delta || ""),
          };
          return {
            ...message,
            status: String(event.data.status || message.status) as ChatMessage["status"],
            content: String(event.data.content ?? message.content),
            error: String(event.data.error || ""),
            usage: (event.data.usage || message.usage) as ChatMessage["usage"],
          };
        }),
      };
    });
  };

  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    const content = input.trim();
    if (!detail || !content || busy) return;
    setBusy(true);
    setError("");
    setInput("");
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      await streamChat(detail.session_id, content, controller.signal, applyStreamEvent);
      await loadSessions(detail.session_id);
    } catch (exc) {
      if (!(exc instanceof DOMException && exc.name === "AbortError")) setError(errorMessage(exc));
      await loadSessions(detail.session_id).catch(() => undefined);
    } finally {
      controllerRef.current = null;
      setBusy(false);
    }
  };

  const stop = async () => {
    if (!detail) return;
    setError("");
    try {
      await apiClient(`/ai/chat/sessions/${detail.session_id}/stop`, { method: "POST" });
      controllerRef.current?.abort();
      window.setTimeout(() => void loadSessions(detail.session_id), 250);
    } catch (exc) {
      setError(errorMessage(exc));
    }
  };

  if (loading) return <div className="loading">正在加载AI聊天中心…</div>;

  return (
    <>
      <div className="page-title">
        <div>
          <h1>AI聊天中心</h1>
          <p className="muted">通过工作区已启用的AI Provider进行安全的多轮聊天</p>
        </div>
        <button className="primary" disabled={busy} onClick={() => void createSession()}>新建会话</button>
      </div>
      {error && <div className="alert error">{error}</div>}

      <div className="chat-layout">
        <aside className="chat-session-list panel">
          <h2>我的会话</h2>
          {!sessions.length && <div className="empty">暂无会话</div>}
          {sessions.map((item) => (
            <div key={item.session_id} className={`chat-session-item ${detail?.session_id === item.session_id ? "active" : ""}`}>
              <button className="chat-session-open" onClick={() => void openSession(item.session_id)}>
                <strong>{item.title}</strong>
                <span className="internal-only">{item.model}</span>
                <span>{formatTime(item.updated_at)}</span>
              </button>
              <button className="chat-delete" title="删除会话" disabled={item.is_running} onClick={() => void removeSession(item)}>×</button>
            </div>
          ))}
        </aside>

        <section className="chat-main panel">
          {!detail ? (
            <div className="chat-welcome">
              <h2>开始新的AI对话</h2>
              <p className="muted">系统将使用管理员分配的 AI 配置。</p>
            </div>
          ) : (
            <>
              <header className="chat-header">
                <div>
                  <h2>{detail.title}</h2>
                  <span className="muted">管理员已配置</span>
                </div>
                <div className="chat-usage internal-only">
                  输入 {detail.usage.prompt_tokens} · 输出 {detail.usage.completion_tokens} · 总计 {detail.usage.total_tokens} Token
                </div>
              </header>
              <div className="chat-messages">
                {detail.messages.filter((item) => item.role !== "system").map((message) => (
                  <article key={message.message_id} className={`chat-message ${message.role}`}>
                    <div className="chat-message-meta">
                      <strong>{message.role === "user" ? "你" : "AI助手"}</strong>
                      <span>{message.status}</span>
                    </div>
                    <div className="chat-message-content">{message.content || (message.status === "STREAMING" ? "正在生成…" : "")}</div>
                    {message.error && <div className="chat-message-error">{message.error}</div>}
                    {message.role === "assistant" && message.usage.total_tokens > 0 && (
                      <div className="muted internal-only">{message.usage.total_tokens} Token · {message.usage.latency_ms} ms</div>
                    )}
                  </article>
                ))}
                <div ref={messageEndRef} />
              </div>
              <form className="chat-composer" onSubmit={send}>
                <textarea
                  value={input}
                  disabled={busy}
                  placeholder="输入消息，Ctrl + Enter发送"
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.ctrlKey && event.key === "Enter") event.currentTarget.form?.requestSubmit();
                  }}
                />
                <div>
                  {busy || detail.is_running
                    ? <button type="button" className="chat-stop" onClick={() => void stop()}>停止生成</button>
                    : <button className="primary" disabled={!input.trim()}>发送</button>}
                </div>
              </form>
            </>
          )}
        </section>
      </div>
    </>
  );
}
