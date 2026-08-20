import { useEffect, useMemo, useState } from "react";
import { apiClient, ApiError, jsonBody } from "../api/client";
import type { Account, AIAnalysis, AIProvider, Page } from "../types";


const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : "请求失败，请稍后重试";

const formatTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

const analysisNames: Record<string, string> = {
  ACCOUNT: "账号健康",
  KEYWORD: "关键词",
};

export function AIAnalysisPage() {
  const [mode, setMode] = useState<"ACCOUNT" | "KEYWORD">("ACCOUNT");
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [providerId, setProviderId] = useState("");
  const [accountId, setAccountId] = useState("");
  const [lookbackDays, setLookbackDays] = useState(30);
  const [keywords, setKeywords] = useState("");
  const [inputText, setInputText] = useState("");
  const [title, setTitle] = useState("");
  const [items, setItems] = useState<AIAnalysis[]>([]);
  const [selected, setSelected] = useState<AIAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selectedAccount = useMemo(
    () => accounts.find((item) => item.id === accountId),
    [accounts, accountId],
  );

  const visibleProviders = useMemo(() => {
    if (!selectedAccount) return providers;
    const matching = providers.filter((item) => item.workspace_id === selectedAccount.workspace_id);
    return matching.length ? matching : providers;
  }, [providers, selectedAccount]);

  const loadHistory = async () => {
    const page = await apiClient<Page<AIAnalysis>>("/ai/analysis?page=1&page_size=50");
    setItems(page.items);
  };

  useEffect(() => {
    Promise.all([
      apiClient<Page<AIProvider>>("/ai/providers?paged=true&page=1&page_size=100&status=ENABLED"),
      apiClient<Page<Account>>("/accounts?paged=true&page=1&page_size=100"),
      apiClient<Page<AIAnalysis>>("/ai/analysis?page=1&page_size=50"),
    ])
      .then(([providerPage, accountPage, analysisPage]) => {
        setProviders(providerPage.items);
        setAccounts(accountPage.items);
        setItems(analysisPage.items);
        const preferred = providerPage.items.find((item) => item.is_default) || providerPage.items[0];
        setProviderId(preferred?.provider_id || "");
        setAccountId(accountPage.items[0]?.id || "");
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

  const openDetail = async (item: AIAnalysis) => {
    setError("");
    try {
      setSelected(await apiClient<AIAnalysis>(`/ai/analysis/${item.analysis_id}`));
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
      let created: AIAnalysis;
      if (mode === "ACCOUNT") {
        created = await apiClient<AIAnalysis>(
          "/ai/analysis/account",
          jsonBody({ account_id: accountId, provider_id: providerId, lookback_days: lookbackDays }),
          180000,
        );
      } else {
        const values = keywords.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean);
        created = await apiClient<AIAnalysis>(
          "/ai/analysis/keywords",
          jsonBody({
            provider_id: providerId,
            account_id: accountId || null,
            keywords: values,
            input_text: inputText,
            title,
            lookback_days: lookbackDays,
          }),
          180000,
        );
      }
      setSelected(created);
      setMessage("分析完成，结果已保存");
      await loadHistory();
    } catch (exc) {
      setError(errorText(exc));
      await loadHistory().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (item: AIAnalysis) => {
    if (!window.confirm("确定删除这条分析记录吗？此操作不可恢复。")) return;
    setError("");
    try {
      await apiClient(`/ai/analysis/${item.analysis_id}`, { method: "DELETE" });
      if (selected?.analysis_id === item.analysis_id) setSelected(null);
      setMessage("分析记录已删除");
      await loadHistory();
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  return (
    <>
      <div className="page-title">
        <div>
          <h1>AI 分析</h1>
          <p className="muted">分析账号运行健康和指定文本关键词；当前不包含 X 帖子、粉丝或曝光数据。</p>
        </div>
        <button onClick={() => void loadHistory()} disabled={loading || busy}>刷新记录</button>
      </div>

      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      {!providers.length && <div className="alert">请先启用一个文字模型 AI Provider。</div>}

      <div className="analysis-tabs">
        <button className={mode === "ACCOUNT" ? "active" : ""} onClick={() => setMode("ACCOUNT")}>账号健康分析</button>
        <button className={mode === "KEYWORD" ? "active" : ""} onClick={() => setMode("KEYWORD")}>关键词分析</button>
      </div>

      <form className="panel analysis-form" onSubmit={submit}>
        <div className="analysis-form-grid">
          <label>
            AI Provider
            <select value={providerId} disabled={busy} onChange={(event) => setProviderId(event.target.value)} required>
              {!visibleProviders.length && <option value="">暂无可用 Provider</option>}
              {visibleProviders.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.name} · {item.default_model}</option>)}
            </select>
          </label>
          <label>
            {mode === "ACCOUNT" ? "分析账号" : "附加账号活动（可选）"}
            <select value={accountId} disabled={busy} onChange={(event) => setAccountId(event.target.value)} required={mode === "ACCOUNT"}>
              {mode === "KEYWORD" && <option value="">不使用账号活动</option>}
              {accounts.map((item) => <option key={item.id} value={item.id}>{item.x_username || item.profile_id} · {item.account_status}</option>)}
            </select>
          </label>
          <label>
            活动回看范围
            <select value={lookbackDays} disabled={busy} onChange={(event) => setLookbackDays(Number(event.target.value))}>
              <option value={7}>最近 7 天</option>
              <option value={30}>最近 30 天</option>
              <option value={90}>最近 90 天</option>
              <option value={365}>最近 365 天</option>
            </select>
          </label>
        </div>

        {mode === "ACCOUNT" ? (
          <div className="analysis-note">
            将分析登录状态、浏览器状态、账号状态、自动化活动成功率和耗时。样本不足时会在结果中明确提示。
          </div>
        ) : (
          <>
            <div className="analysis-form-grid keyword-grid">
              <label>
                关键词（逗号或换行分隔，最多 20 个）
                <input value={keywords} maxLength={1600} disabled={busy} onChange={(event) => setKeywords(event.target.value)} placeholder="品牌词, 产品词, 竞品词" required />
              </label>
              <label>
                分析标题（可选）
                <input value={title} maxLength={160} disabled={busy} onChange={(event) => setTitle(event.target.value)} placeholder="例如：8月品牌关键词检查" />
              </label>
            </div>
            <label>
              待分析文本
              <textarea value={inputText} maxLength={20000} disabled={busy} onChange={(event) => setInputText(event.target.value)} placeholder="粘贴帖子、评论、客服记录或其他文本。也可以选择账号，附加分析已有活动记录。" />
              <span className="muted">{inputText.length} / 20000</span>
            </label>
          </>
        )}

        <div className="analysis-submit-row">
          <span className="muted">分析会调用所选模型并产生相应 API 费用。</span>
          <button className="primary" disabled={busy || !providerId || (mode === "ACCOUNT" && !accountId) || (mode === "KEYWORD" && !keywords.trim() || (mode === "KEYWORD" && !inputText.trim() && !accountId))}>
            {busy ? "正在分析，请稍候…" : "开始分析"}
          </button>
        </div>
      </form>

      <div className="analysis-layout">
        <section className="panel analysis-history">
          <h2>分析历史</h2>
          {loading ? <div className="loading">正在加载…</div> : items.map((item) => (
            <article className={`analysis-history-item ${selected?.analysis_id === item.analysis_id ? "active" : ""}`} key={item.analysis_id}>
              <button className="analysis-history-open" onClick={() => void openDetail(item)}>
                <strong>{item.title}</strong>
                <span>{analysisNames[item.analysis_type]} · {item.status} · {formatTime(item.created_at)}</span>
                <small>{item.summary || item.error || "等待结果"}</small>
              </button>
              <button className="analysis-delete" onClick={() => void remove(item)}>×</button>
            </article>
          ))}
          {!loading && !items.length && <div className="empty">还没有分析记录</div>}
        </section>

        <section className="panel analysis-result">
          {selected ? (
            <>
              <div className="analysis-result-head">
                <div><h2>{selected.title}</h2><span className="muted">{selected.provider_name} · {selected.model}</span></div>
                <span className={`state state-${selected.status.toLowerCase()}`}>{selected.status}</span>
              </div>
              {selected.error && <div className="alert error">{selected.error}</div>}
              <div className="analysis-metrics">
                <span>输入 Token <strong>{selected.prompt_tokens}</strong></span>
                <span>输出 Token <strong>{selected.completion_tokens}</strong></span>
                <span>总 Token <strong>{selected.total_tokens}</strong></span>
                <span>耗时 <strong>{(selected.latency_ms / 1000).toFixed(1)}s</strong></span>
              </div>
              {selected.summary && <div className="analysis-summary">{selected.summary}</div>}
              <h2>结构化结果</h2>
              <pre className="analysis-json">{JSON.stringify(selected.result || {}, null, 2)}</pre>
              <details>
                <summary>查看数据快照</summary>
                <pre className="analysis-json secondary">{JSON.stringify(selected.source_snapshot || {}, null, 2)}</pre>
              </details>
            </>
          ) : <div className="analysis-welcome"><h2>选择一条记录查看结果</h2><p className="muted">或者在上方发起新的分析。</p></div>}
        </section>
      </div>
    </>
  );
}
