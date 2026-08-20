import { useEffect, useMemo, useState } from "react";
import { apiBlob, apiClient, ApiError, jsonBody } from "../api/client";
import type { AIImage, AIProvider, Page } from "../types";


const formatTime = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

const formatBytes = (value: number) => {
  if (!value) return "-";
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(2)} MB`;
};

const qualityNames: Record<string, string> = {
  low: "低（草稿，速度快）",
  medium: "中（推荐）",
  high: "高（更慢、费用更高）",
};

const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : "请求失败，请稍后重试";

export function AIImagesPage() {
  const [providers, setProviders] = useState<AIProvider[]>([]);
  const [providerId, setProviderId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [resolution, setResolution] = useState<"1K" | "2K">("1K");
  const [quality, setQuality] = useState<"low" | "medium" | "high">("medium");
  const [items, setItems] = useState<AIImage[]>([]);
  const [imageUrls, setImageUrls] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(0);
  const [total, setTotal] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const selectedProvider = useMemo(
    () => providers.find((item) => item.provider_id === providerId),
    [providers, providerId],
  );

  const loadImages = async (targetPage = page) => {
    setLoading(true);
    try {
      const data = await apiClient<Page<AIImage>>(`/ai/images?page=${targetPage}&page_size=12`);
      setItems(data.items);
      setPages(data.pages);
      setTotal(data.total);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    apiClient<Page<AIProvider>>("/ai/providers?paged=true&page=1&page_size=100&status=ENABLED")
      .then((data) => {
        const enabled = data.items.filter((item) => item.status === "ENABLED");
        setProviders(enabled);
        const preferred = enabled.find((item) => item.is_default) || enabled[0];
        setProviderId(preferred?.provider_id || "");
      })
      .catch((exc) => setError(errorText(exc)));
  }, []);

  useEffect(() => {
    void loadImages(page);
  }, [page]);

  useEffect(() => {
    let active = true;
    const created: string[] = [];
    Promise.all(
      items
        .filter((item) => item.status === "SUCCESS" && item.content_url)
        .map(async (item) => {
          try {
            const blob = await apiBlob(item.content_url.replace(/^\/api/, ""));
            const url = URL.createObjectURL(blob);
            created.push(url);
            return [item.image_id, url] as const;
          } catch {
            return null;
          }
        }),
    ).then((entries) => {
      if (!active) {
        created.forEach((url) => URL.revokeObjectURL(url));
        return;
      }
      setImageUrls(Object.fromEntries(entries.filter((item): item is readonly [string, string] => Boolean(item))));
    });
    return () => {
      active = false;
      created.forEach((url) => URL.revokeObjectURL(url));
    };
  }, [items]);

  const generate = async (event: React.FormEvent) => {
    event.preventDefault();
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt || !providerId) return;
    setError("");
    setMessage("");
    setGenerating(true);
    try {
      await apiClient<AIImage>(
        "/ai/images/generate",
        jsonBody({ provider_id: providerId, prompt: cleanPrompt, resolution, quality }),
        360000,
      );
      setMessage("图片生成成功，已安全保存到服务器");
      setPrompt("");
      if (page !== 1) setPage(1);
      else await loadImages(1);
    } catch (exc) {
      setError(errorText(exc));
      await loadImages(page);
    } finally {
      setGenerating(false);
    }
  };

  const remove = async (item: AIImage) => {
    if (!window.confirm("确定删除这张图片和服务器文件吗？此操作不可恢复。")) return;
    setError("");
    try {
      await apiClient(`/ai/images/${item.image_id}`, { method: "DELETE" });
      setMessage("图片已删除");
      await loadImages(page);
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  const download = (item: AIImage) => {
    const url = imageUrls[item.image_id];
    if (!url) return;
    const link = document.createElement("a");
    link.href = url;
    const extension = item.mime_type === "image/jpeg" ? "jpg" : item.mime_type === "image/webp" ? "webp" : "png";
    link.download = `laogu-${item.resolution}-${item.image_id}.${extension}`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  return (
    <>
      <div className="page-title">
        <div>
          <h1>AI 生图</h1>
          <p className="muted">独立调用 gpt-image-2，每次生成一张图片；与 AI 聊天模型互不影响。</p>
        </div>
        <button onClick={() => void loadImages(page)} disabled={loading || generating}>刷新图库</button>
      </div>

      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      {!providers.length && <div className="alert">请先到“AI 服务商”页面启用一个支持 gpt-image-2 的 Provider。</div>}

      <form className="panel image-generate-panel" onSubmit={generate}>
        <div className="image-form-head">
          <div>
            <h2>生成新图片</h2>
            <span className="muted">生成通常需要几十秒，复杂提示词可能接近 2 分钟。</span>
          </div>
          <span className="state state-enabled">固定模型：gpt-image-2</span>
        </div>
        <label>
          图片描述
          <textarea
            value={prompt}
            maxLength={4000}
            disabled={generating}
            placeholder="例如：一只戴橙色围巾的水獭坐在湖边，电影感光线，细节丰富"
            onChange={(event) => setPrompt(event.target.value)}
          />
          <span className="muted">{prompt.length} / 4000</span>
        </label>
        <div className="image-options">
          <label>
            Provider
            <select value={providerId} disabled={generating} onChange={(event) => setProviderId(event.target.value)}>
              {!providers.length && <option value="">暂无可用 Provider</option>}
              {providers.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.name}{item.is_default ? "（默认）" : ""}</option>)}
            </select>
          </label>
          <label>
            分辨率
            <select value={resolution} disabled={generating} onChange={(event) => setResolution(event.target.value as "1K" | "2K")}>
              <option value="1K">1K · 1024 × 1024</option>
              <option value="2K">2K · 2048 × 2048（费用更高）</option>
            </select>
          </label>
          <label>
            质量
            <select value={quality} disabled={generating} onChange={(event) => setQuality(event.target.value as "low" | "medium" | "high")}>
              {Object.entries(qualityNames).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <button className="primary image-generate-button" disabled={generating || !prompt.trim() || !providerId}>
            {generating ? "正在生成，请稍候…" : "生成图片"}
          </button>
        </div>
        {selectedProvider && <div className="muted">请求将发送到：{selectedProvider.name} · {selectedProvider.base_url}</div>}
      </form>

      <div className="image-gallery-header">
        <div><h2>我的图库</h2><span className="muted">共 {total} 张，仅当前用户可见</span></div>
      </div>
      {loading && !items.length ? <div className="loading">正在加载图库…</div> : (
        <div className="image-gallery">
          {items.map((item) => (
            <article className="image-card" key={item.image_id}>
              <div className="image-preview">
                {imageUrls[item.image_id]
                  ? <img src={imageUrls[item.image_id]} alt={item.prompt} />
                  : <div className={`image-placeholder ${item.status.toLowerCase()}`}>{item.status === "FAILED" ? "生成失败" : item.status === "PENDING" ? "生成中…" : "加载图片…"}</div>}
              </div>
              <div className="image-card-body">
                <p className="image-prompt" title={item.prompt}>{item.prompt}</p>
                <div className="image-meta">
                  <span>{item.resolution} · {item.size}</span>
                  <span>{qualityNames[item.quality] || item.quality}</span>
                  <span>{formatBytes(item.byte_size)} · {(item.latency_ms / 1000).toFixed(1)}s</span>
                  <span>{formatTime(item.created_at)}</span>
                </div>
                {item.error && <div className="chat-message-error">{item.error}</div>}
                <div className="image-actions">
                  <button disabled={!imageUrls[item.image_id]} onClick={() => download(item)}>下载</button>
                  <button className="danger-button" onClick={() => void remove(item)}>删除</button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
      {!loading && !items.length && <div className="empty">还没有生成图片</div>}
      {pages > 1 && (
        <div className="pager">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span>{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
    </>
  );
}
