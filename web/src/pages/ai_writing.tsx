import { useEffect, useMemo, useState } from "react";
import { apiClient, ApiError, jsonBody } from "../api/client";
import type { Account, AIProvider, AIWritingRecord, AIWritingReply, Page } from "../types";


const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : "请求失败，请稍后重试";

const formatTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

export function AIWritingPage() {
  const [mode, setMode] = useState<"ANALYSIS" | "REPLY">("ANALYSIS");
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [providerId, setProviderId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [contextText, setContextText] = useState("");
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [brandVoice, setBrandVoice] = useState("");
  const [tone, setTone] = useState("FRIENDLY");
  const [language, setLanguage] = useState("AUTO");
  const [variantCount, setVariantCount] = useState(3);
  const [maxCharacters, setMaxCharacters] = useState(280);
  const [items, setItems] = useState<AIWritingRecord[]>([]);
  const [selected, setSelected] = useState<AIWritingRecord | null>(null);
  const [copied, setCopied] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selectedAccount = useMemo(() => accounts.find((item) => item.id === accountId), [accounts, accountId]);
  const visibleProviders = useMemo(() => {
    if (!selectedAccount) return providers;
    const matching = providers.filter((item) => item.workspace_id === selectedAccount.workspace_id);
    return matching.length ? matching : providers;
  }, [providers, selectedAccount]);

  const loadHistory = async () => {
    const page = await apiClient<Page<AIWritingRecord>>("/ai/writing?page=1&page_size=50");
    setItems(page.items);
  };

  useEffect(() => {
    Promise.all([
      apiClient<Page<AIProvider>>("/ai/providers?paged=true&page=1&page_size=100&status=ENABLED"),
      apiClient<Page<Account>>("/accounts?paged=true&page=1&page_size=100"),
      apiClient<Page<AIWritingRecord>>("/ai/writing?page=1&page_size=50"),
    ])
      .then(([providerPage, accountPage, writingPage]) => {
        setProviders(providerPage.items);
        setAccounts(accountPage.items);
        setItems(writingPage.items);
        const preferred = providerPage.items.find((item) => item.is_default) || providerPage.items[0];
        setProviderId(preferred?.provider_id || "");
      })
      .catch((exc) => setError(errorText(exc)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!visibleProviders.some((item) => item.provider_id === providerId)) {
      const preferred = visibleProviders.find((item) => item.is_default) || visibleProviders[0];
      setProviderId(preferred?.provider_id || "");
    }
  }, [visibleProviders, providerId]);

  const openDetail = async (item: AIWritingRecord) => {
    setError("");
    try {
      setSelected(await apiClient<AIWritingRecord>(`/ai/writing/${item.record_id}`));
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const common = {
        source_text: sourceText,
        context_text: contextText,
        provider_id: providerId,
        account_id: accountId || null,
        title,
      };
      const created = mode === "ANALYSIS"
        ? await apiClient<AIWritingRecord>("/ai/writing/analyze", jsonBody(common), 180000)
        : await apiClient<AIWritingRecord>("/ai/writing/replies", jsonBody({
            ...common,
            objective,
            brand_voice: brandVoice,
            tone,
            language,
            variant_count: variantCount,
            max_characters: maxCharacters,
          }), 180000);
      setSelected(created);
      setMessage(mode === "ANALYSIS" ? "话术分析完成，结果已保存" : "回复草稿生成完成，请人工审核后再使用");
      await loadHistory();
    } catch (exc) {
      setError(errorText(exc));
      await loadHistory().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (item: AIWritingRecord) => {
    if (!window.confirm("确定删除这条话术记录吗？此操作不可恢复。")) return;
    try {
      await apiClient(`/ai/writing/${item.record_id}`, { method: "DELETE" });
      if (selected?.record_id === item.record_id) setSelected(null);
      setMessage("话术记录已删除");
      await loadHistory();
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  const copyReply = async (reply: AIWritingReply, index: number) => {
    try {
      await navigator.clipboard.writeText(reply.text);
      setCopied(String(index));
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      setError("复制失败，请手动选择文本复制");
    }
  };

  const replies = selected?.result?.replies || [];

  return (
    <>
      <div className="page-title">
        <div>
          <h1>AI 话术</h1>
          <p className="muted">分析原文意图并生成候选回复；所有回复仅为草稿，不会自动发布。</p>
        </div>
        <button onClick={() => void loadHistory()} disabled={loading || busy}>刷新记录</button>
      </div>
      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      {!providers.length && <div className="alert">请先启用一个文字模型 AI Provider。</div>}

      <div className="analysis-tabs">
        <button className={mode === "ANALYSIS" ? "active" : ""} onClick={() => setMode("ANALYSIS")}>话术分析</button>
        <button className={mode === "REPLY" ? "active" : ""} onClick={() => setMode("REPLY")}>回复生成</button>
      </div>

      <form className="panel writing-form" onSubmit={submit}>
        <div className="writing-form-grid">
          <label>
            AI Provider
            <select value={providerId} disabled={busy} onChange={(event) => setProviderId(event.target.value)} required>
              {!visibleProviders.length && <option value="">暂无可用 Provider</option>}
              {visibleProviders.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.name} · {item.default_model}</option>)}
            </select>
          </label>
          <label>
            关联账号（可选）
            <select value={accountId} disabled={busy} onChange={(event) => setAccountId(event.target.value)}>
              <option value="">不关联账号</option>
              {accounts.map((item) => <option key={item.id} value={item.id}>{item.x_username || item.profile_id} · {item.account_status}</option>)}
            </select>
          </label>
          <label>
            记录标题（可选）
            <input value={title} maxLength={160} disabled={busy} onChange={(event) => setTitle(event.target.value)} />
          </label>
        </div>
        <div className="writing-text-grid">
          <label>
            原帖、评论或消息
            <textarea value={sourceText} maxLength={10000} disabled={busy} required onChange={(event) => setSourceText(event.target.value)} placeholder="粘贴需要分析或回复的原文" />
            <span className="muted">{sourceText.length} / 10000</span>
          </label>
          <label>
            补充上下文（可选）
            <textarea value={contextText} maxLength={10000} disabled={busy} onChange={(event) => setContextText(event.target.value)} placeholder="例如：产品背景、已知事实、不能承诺的事项" />
            <span className="muted">{contextText.length} / 10000</span>
          </label>
        </div>

        {mode === "REPLY" && (
          <>
            <div className="writing-reply-grid">
              <label>回复目标<input value={objective} maxLength={500} disabled={busy} onChange={(event) => setObjective(event.target.value)} placeholder="例如：礼貌解释并邀请私信提供订单号" /></label>
              <label>语气<select value={tone} disabled={busy} onChange={(event) => setTone(event.target.value)}><option value="FRIENDLY">友好自然</option><option value="PROFESSIONAL">专业克制</option><option value="CONCISE">简洁直接</option><option value="PERSUASIVE">有说服力</option></select></label>
              <label>语言<select value={language} disabled={busy} onChange={(event) => setLanguage(event.target.value)}><option value="AUTO">跟随原文</option><option value="ZH">简体中文</option><option value="EN">English</option></select></label>
              <label>候选数量<select value={variantCount} disabled={busy} onChange={(event) => setVariantCount(Number(event.target.value))}>{[1,2,3,4,5].map((value) => <option key={value} value={value}>{value} 条</option>)}</select></label>
              <label>每条最大字符<input type="number" min={40} max={1000} value={maxCharacters} disabled={busy} onChange={(event) => setMaxCharacters(Number(event.target.value))} /></label>
            </div>
            <label>
              品牌语气或账号人设（可选）
              <textarea className="writing-brand-voice" value={brandVoice} maxLength={2000} disabled={busy} onChange={(event) => setBrandVoice(event.target.value)} placeholder="例如：专业但不生硬，不使用夸张承诺，不与用户争辩" />
            </label>
          </>
        )}

        <div className="analysis-submit-row">
          <span className="muted">将调用所选模型并产生 API 费用；回复必须人工审核后使用。</span>
          <button className="primary" disabled={busy || !providerId || !sourceText.trim()}>{busy ? "正在处理，请稍候…" : mode === "ANALYSIS" ? "开始分析" : "生成回复草稿"}</button>
        </div>
      </form>

      <div className="analysis-layout writing-layout">
        <section className="panel analysis-history">
          <h2>话术历史</h2>
          {loading ? <div className="loading">正在加载…</div> : items.map((item) => (
            <article className={`analysis-history-item ${selected?.record_id === item.record_id ? "active" : ""}`} key={item.record_id}>
              <button className="analysis-history-open" onClick={() => void openDetail(item)}>
                <strong>{item.title}</strong>
                <span>{item.record_type === "ANALYSIS" ? "话术分析" : "回复草稿"} · {item.status} · {formatTime(item.created_at)}</span>
                <small>{item.summary || item.error || "等待结果"}</small>
              </button>
              <button className="analysis-delete" onClick={() => void remove(item)}>×</button>
            </article>
          ))}
          {!loading && !items.length && <div className="empty">还没有话术记录</div>}
        </section>

        <section className="panel analysis-result writing-result">
          {selected ? (
            <>
              <div className="analysis-result-head">
                <div><h2>{selected.title}</h2><span className="muted">{selected.provider_name} · {selected.model}</span></div>
                <span className={`state state-${selected.status.toLowerCase()}`}>{selected.status}</span>
              </div>
              {selected.error && <div className="alert error">{selected.error}</div>}
              <div className="analysis-metrics">
                <span>输入 Token<strong>{selected.prompt_tokens}</strong></span><span>输出 Token<strong>{selected.completion_tokens}</strong></span><span>总 Token<strong>{selected.total_tokens}</strong></span><span>耗时<strong>{(selected.latency_ms / 1000).toFixed(1)}s</strong></span>
              </div>
              {selected.summary && <div className="analysis-summary">{selected.summary}</div>}
              {selected.record_type === "REPLY" && replies.length > 0 ? (
                <div className="reply-draft-list">
                  {replies.map((reply, index) => (
                    <article className="reply-draft" key={`${index}-${reply.text}`}>
                      <div className="reply-draft-head"><strong>候选回复 {index + 1}</strong><span>{reply.character_count} 字符 · {reply.tone}</span></div>
                      <p>{reply.text}</p>
                      {reply.reason && <small>{reply.reason}</small>}
                      <button onClick={() => void copyReply(reply, index)}>{copied === String(index) ? "已复制" : "复制回复"}</button>
                    </article>
                  ))}
                  <div className="alert writing-review-alert">候选回复尚未发布，请核对事实、语气和平台规则后人工使用。</div>
                </div>
              ) : <pre className="analysis-json">{JSON.stringify(selected.result || {}, null, 2)}</pre>}
              <details><summary>查看输入和参数</summary><pre className="analysis-json secondary">{JSON.stringify({ source_text: selected.source_text, context_text: selected.context_text, parameters: selected.parameters }, null, 2)}</pre></details>
            </>
          ) : <div className="analysis-welcome"><h2>选择一条记录查看结果</h2><p className="muted">AI仅生成草稿，不会自动发送或发布。</p></div>}
        </section>
      </div>
    </>
  );
}
