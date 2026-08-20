import { useEffect, useMemo, useState } from "react";
import { apiClient, ApiError, jsonBody } from "../api/client";
import type { AIProvider, AITaskProposal, Page } from "../types";

const errorText = (value: unknown) => value instanceof ApiError ? value.message : "请求失败，请稍后重试";
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "-";

export function AITasksPage() {
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [items, setItems] = useState<AITaskProposal[]>([]);
  const [selected, setSelected] = useState<AITaskProposal | null>(null);
  const [providerId, setProviderId] = useState("");
  const [requestText, setRequestText] = useState("");
  const [timeout, setTimeoutValue] = useState(60);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selectedProvider = useMemo(() => providers.find((item) => item.provider_id === providerId), [providers, providerId]);

  const loadHistory = async () => {
    const page = await apiClient<Page<AITaskProposal>>("/ai/task-proposals?page=1&page_size=50");
    setItems(page.items);
  };

  useEffect(() => {
    Promise.all([
      apiClient<Page<AIProvider>>("/ai/providers?paged=true&page=1&page_size=100&status=ENABLED"),
      apiClient<Page<AITaskProposal>>("/ai/task-proposals?page=1&page_size=50"),
    ])
      .then(([providerPage, proposalPage]) => {
        setProviders(providerPage.items);
        setItems(proposalPage.items);
        const preferred = providerPage.items.find((item) => item.is_default) || providerPage.items[0];
        setProviderId(preferred?.provider_id || "");
      })
      .catch((exc) => setError(errorText(exc)))
      .finally(() => setLoading(false));
  }, []);

  const openDetail = async (item: AITaskProposal) => {
    setError("");
    try { setSelected(await apiClient<AITaskProposal>(`/ai/task-proposals/${item.proposal_id}`)); }
    catch (exc) { setError(errorText(exc)); }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(""); setMessage("");
    try {
      const created = await apiClient<AITaskProposal>("/ai/task-proposals", jsonBody({
        request_text: requestText, provider_id: providerId || null, timeout,
      }), 180000);
      setSelected(created); setRequestText("");
      setMessage("任务提案已生成。确认前不会创建任务，也不会让Agent执行。");
      await loadHistory();
    } catch (exc) {
      setError(errorText(exc));
      await loadHistory().catch(() => undefined);
    } finally { setBusy(false); }
  };

  const changeProposal = async (action: "confirm" | "reject") => {
    if (!selected || busy) return;
    if (action === "reject" && !window.confirm("确定拒绝这份任务提案吗？拒绝后不能再次确认。")) return;
    if (action === "confirm" && !window.confirm("确认后将为提案中的每个Profile创建 script.execute 任务，Windows Agent随后可能拉取执行。继续吗？")) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const updated = await apiClient<AITaskProposal>(`/ai/task-proposals/${selected.proposal_id}/${action}`, { method: "POST" });
      setSelected(updated);
      setMessage(action === "confirm" ? `已确认并创建 ${updated.task_ids.length} 个任务。` : "提案已拒绝，未创建任务。");
      await loadHistory();
    } catch (exc) { setError(errorText(exc)); }
    finally { setBusy(false); }
  };

  const plan = selected?.plan || {};
  return <>
    <div className="page-title">
      <div><h1>AI 任务</h1><p className="muted">让AI从已启用脚本和现有Profile中生成任务提案，再由你确认创建。</p></div>
      <button onClick={() => void loadHistory()} disabled={loading || busy}>刷新记录</button>
    </div>
    {error && <div className="alert error">{error}</div>}
    {message && <div className="alert">{message}</div>}
    {!providers.length && <div className="alert">请先启用一个文本模型 AI Provider。</div>}

    <form className="panel ai-task-form" onSubmit={submit}>
      <div className="form-grid ai-task-form-grid">
        <label>AI Provider<select value={providerId} disabled={busy} onChange={(event) => setProviderId(event.target.value)} required>
          {!providers.length && <option value="">暂无可用 Provider</option>}
          {providers.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.name} · {item.default_model}</option>)}
        </select></label>
        <label>规划超时<select value={timeout} disabled={busy} onChange={(event) => setTimeoutValue(Number(event.target.value))}><option value={30}>30 秒</option><option value={60}>60 秒</option><option value={120}>120 秒</option><option value={300}>300 秒</option></select></label>
        <div className="ai-task-provider-note">当前模型：{selectedProvider?.default_model || "-"}</div>
      </div>
      <label>自然语言任务要求<textarea value={requestText} onChange={(event) => setRequestText(event.target.value)} disabled={busy} required maxLength={10000} placeholder="例如：让所有已登录账号访问 https://example.com，并把页面标题记录下来。" /></label>
      <div className="analysis-submit-row"><span className="muted">AI只返回计划，不读取或生成脚本源码；确认前不会创建Task。</span><button className="primary" disabled={busy || !providerId || !requestText.trim()}>{busy ? "处理中…" : "生成任务提案"}</button></div>
    </form>

    <div className="analysis-layout ai-task-layout">
      <section className="panel analysis-history"><h2>提案历史</h2>
        {loading ? <div className="loading">正在加载…</div> : items.map((item) => <article className={`analysis-history-item ${selected?.proposal_id === item.proposal_id ? "active" : ""}`} key={item.proposal_id}>
          <button className="analysis-history-open" onClick={() => void openDetail(item)}><strong>{item.summary || item.error || "未命名提案"}</strong><span>{item.status} · {item.model} · {formatTime(item.created_at)}</span><small>{item.plan?.script_name || "尚未匹配脚本"}</small></button>
        </article>)}
        {!loading && !items.length && <div className="empty">还没有任务提案</div>}
      </section>
      <section className="panel analysis-result">
        {selected ? <>
          <div className="analysis-result-head"><div><h2>{selected.summary || "AI任务提案"}</h2><span className="muted">{selected.provider_name} · {selected.model} · {formatTime(selected.created_at)}</span></div><span className={`state state-${selected.status.toLowerCase()}`}>{selected.status}</span></div>
          {selected.error && <div className="alert error">{selected.error}</div>}
          <div className="analysis-metrics"><span>输入 Token<strong>{selected.prompt_tokens}</strong></span><span>输出 Token<strong>{selected.completion_tokens}</strong></span><span>总 Token<strong>{selected.total_tokens}</strong></span><span>耗时<strong>{(selected.latency_ms / 1000).toFixed(1)}s</strong></span></div>
          <div className="ai-task-safety-note">安全提示：确认前不会创建任务；确认后才会创建 <code>script.execute</code>，Windows Agent 才能拉取执行。</div>
          <div className="task-plan-grid"><div><h3>脚本</h3><p>{plan.script_name || "未匹配"} {plan.script_version ? `· v${plan.script_version}` : ""}</p><small className="mono">{plan.script_id || "-"}<br />{plan.script_version_id || "-"}</small></div><div><h3>Profiles</h3><p>{(plan.profile_labels || []).map((item) => item.x_username || item.profile_id).join("、") || "-"}</p></div><div><h3>超时</h3><p>{plan.timeout || 60} 秒</p></div></div>
          {plan.reason && <div className="analysis-summary"><strong>规划理由</strong><br />{plan.reason}</div>}
          {!!plan.risk_notes?.length && <div className="alert warning">风险提示：{plan.risk_notes.join("；")}</div>}
          <h3>参数</h3><pre className="analysis-json">{JSON.stringify(plan.params || {}, null, 2)}</pre>
          {selected.request_text && <details><summary>查看原始要求</summary><p className="task-request">{selected.request_text}</p></details>}
          {selected.status === "DRAFT" && <div className="ai-task-actions"><button className="primary" disabled={busy} onClick={() => void changeProposal("confirm")}>确认并创建任务</button><button className="danger-button" disabled={busy} onClick={() => void changeProposal("reject")}>拒绝提案</button></div>}
          {!!selected.task_ids.length && <div className="alert">已创建任务：{selected.task_ids.map((id) => <span className="mono task-id" key={id}>{id}</span>)}</div>}
        </> : <div className="analysis-welcome"><h2>选择一份提案</h2><p className="muted">AI不会直接执行任务，所有计划都需要人工确认。</p></div>}
      </section>
    </div>
  </>;
}
