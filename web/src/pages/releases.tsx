import { useEffect, useMemo, useRef, useState } from "react";
import { apiClient, ApiError, uploadAppRelease } from "../api/client";
import type { AppReleaseItem, User } from "../types";

const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : "请求失败，请稍后重试";

const formatDate = (value: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "—";

const formatSize = (bytes: number) => {
  if (!bytes || bytes <= 0) return "0 MB";
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(2)} MB`;
};

export function ReleasesPage({ user }: { user: User }) {
  const [items, setItems] = useState<AppReleaseItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // 发布表单状态
  const [version, setVersion] = useState("");
  const [channel, setChannel] = useState("stable");
  const [releaseNotes, setReleaseNotes] = useState("");
  const [isMandatory, setIsMandatory] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  // 清理确认弹窗状态
  const [deleteTarget, setDeleteTarget] = useState<AppReleaseItem | null>(null);
  const [deleting, setDeleting] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    setRefreshing(true);
    try {
      const res = await apiClient<{ items: AppReleaseItem[]; total: number }>(
        "/admin/releases"
      );
      setItems(res.items || []);
      setError("");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  // 统计信息
  const stats = useMemo(() => {
    let totalBytes = 0;
    let inStoreCount = 0;
    let deletedCount = 0;
    for (const item of items) {
      if (item.file_exists) {
        totalBytes += item.file_size || 0;
        inStoreCount++;
      }
      if (item.package_deleted) {
        deletedCount++;
      }
    }
    const latestStable = items.find((i) => i.channel === "stable");
    return {
      totalMB: (totalBytes / (1024 * 1024)).toFixed(1),
      inStoreCount,
      deletedCount,
      latestVersion: latestStable ? latestStable.version : "—",
    };
  }, [items]);

  const handlePublish = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!version.trim()) {
      setError("请填写版本号，例如 0.21.91");
      return;
    }
    if (!selectedFile) {
      setError("请选择待发布的 .zip 升级包文件");
      return;
    }
    setUploading(true);
    setError("");
    setSuccessMsg("");
    try {
      const result = await uploadAppRelease(
        selectedFile,
        version.trim(),
        channel.trim() || "stable",
        releaseNotes.trim(),
        isMandatory
      );
      setSuccessMsg(
        `新版本 ${result.version} (${result.channel}) 发布成功！文件大小: ${formatSize(result.file_size)}`
      );
      setVersion("");
      setReleaseNotes("");
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await load();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setUploading(false);
    }
  };

  const confirmDeletePackage = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    setError("");
    setSuccessMsg("");
    try {
      const query = new URLSearchParams({ channel: deleteTarget.channel });
      const res = await apiClient<{
        ok: boolean;
        version: string;
        released_mb: number;
      }>(
        `/admin/releases/${encodeURIComponent(deleteTarget.version)}/package-file?${query.toString()}`,
        { method: "DELETE" }
      );
      setSuccessMsg(
        `已成功清理版本 ${res.version} 安装包文件，释放了 ${res.released_mb} MB 服务器磁盘空间！`
      );
      setDeleteTarget(null);
      await load();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setDeleting(false);
    }
  };

  if (user.role !== "ADMIN") {
    return (
      <div className="alert error">
        当前页面仅系统管理员可访问。
      </div>
    );
  }

  return (
    <>
      <div className="page-title">
        <div>
          <h1>客户端版本管理</h1>
          <p className="muted">
            发布桌面端升级安装包，支持按需清理历史包释放服务器硬盘
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={refreshing || uploading}
        >
          {refreshing ? "刷新中…" : "刷新列表"}
        </button>
      </div>

      {error && <div className="alert error">{error}</div>}
      {successMsg && <div className="alert success">{successMsg}</div>}

      <div className="metrics control-metrics">
        <div className="metric-card good">
          <span>当前最新稳定版</span>
          <strong>{stats.latestVersion}</strong>
        </div>
        <div className="metric-card">
          <span>在库安装包占用</span>
          <strong>{stats.totalMB} MB</strong>
        </div>
        <div className="metric-card good">
          <span>可用升级包数</span>
          <strong>{stats.inStoreCount} 个</strong>
        </div>
        <div className="metric-card">
          <span>已释放清理包数</span>
          <strong>{stats.deletedCount} 个</strong>
        </div>
      </div>

      <div className="grid-2" style={{ marginBottom: "24px" }}>
        {/* 发布新版本 */}
        <section className="panel">
          <h2>发布客户端新版本</h2>
          <form onSubmit={handlePublish}>
            <div style={{ display: "flex", gap: "12px", marginBottom: "12px" }}>
              <div style={{ flex: 1 }}>
                <label className="muted" style={{ display: "block", marginBottom: "4px" }}>
                  版本号 *
                </label>
                <input
                  type="text"
                  placeholder="例如 0.21.91"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                  disabled={uploading}
                  required
                />
              </div>
              <div style={{ width: "120px" }}>
                <label className="muted" style={{ display: "block", marginBottom: "4px" }}>
                  发布渠道
                </label>
                <select
                  value={channel}
                  onChange={(e) => setChannel(e.target.value)}
                  disabled={uploading}
                >
                  <option value="stable">stable (稳定)</option>
                  <option value="beta">beta (测试)</option>
                </select>
              </div>
            </div>

            <div style={{ marginBottom: "12px" }}>
              <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={isMandatory}
                  onChange={(e) => setIsMandatory(e.target.checked)}
                  disabled={uploading}
                />
                <span>强制更新（用户端不可忽略）</span>
              </label>
            </div>

            <div style={{ marginBottom: "12px" }}>
              <label className="muted" style={{ display: "block", marginBottom: "4px" }}>
                更新日志 / Release Notes
              </label>
              <textarea
                rows={3}
                placeholder="填写本次版本的更新内容亮点..."
                value={releaseNotes}
                onChange={(e) => setReleaseNotes(e.target.value)}
                disabled={uploading}
              />
            </div>

            <div style={{ marginBottom: "16px" }}>
              <label className="muted" style={{ display: "block", marginBottom: "4px" }}>
                选择升级包文件 (.zip) *
              </label>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,application/zip"
                disabled={uploading}
                onChange={(e) => {
                  const file = e.target.files?.[0] || null;
                  setSelectedFile(file);
                }}
              />
              {selectedFile && (
                <small className="muted" style={{ display: "block", marginTop: "4px" }}>
                  已选文件: {selectedFile.name} ({formatSize(selectedFile.size)})
                </small>
              )}
            </div>

            <button
              type="submit"
              className="primary"
              disabled={uploading || !selectedFile || !version.trim()}
            >
              {uploading ? "正在上传发布中，请稍候…" : "发布升级包"}
            </button>
          </form>
        </section>

        {/* 说明面板 */}
        <section className="panel">
          <h2>版本发布与硬盘管理指引</h2>
          <ul style={{ paddingLeft: "20px", lineHeight: "1.8", color: "var(--text-muted, #888)" }}>
            <li>
              <strong>客户端自动检测</strong>：客户端打开或通过菜单栏点击【检查更新】时，会自动请求最新版本并提示用户升级。
            </li>
            <li>
              <strong>硬盘空间释放</strong>：长期维护可能产生较多历史更新包，导致服务器硬盘占满。点击右侧列表中历史版本的【清理安装包】即可物理删除该大文件，立即释放服务器硬盘。
            </li>
            <li>
              <strong>日志保留</strong>：删除历史包后，版本记录与更新说明继续保留，管理列表直观清晰。
            </li>
            <li>
              <strong>防误删安全</strong>：已被清理的版本不会被推送给客户端下载，防止下载报错。
            </li>
          </ul>
        </section>
      </div>

      {/* 已发布版本列表 */}
      <section className="panel">
        <h2>已发布版本列表</h2>
        {loading ? (
          <div className="loading">正在加载版本列表…</div>
        ) : items.length === 0 ? (
          <p className="muted" style={{ padding: "20px 0", textAlign: "center" }}>
            暂未发布任何客户端版本
          </p>
        ) : (
          <div style={{ overflowX: "auto" }}>
            <table className="table" style={{ width: "100%" }}>
              <thead>
                <tr>
                  <th>版本号</th>
                  <th>渠道</th>
                  <th>安装包体积</th>
                  <th>更新说明</th>
                  <th>发布时间</th>
                  <th>文件状态</th>
                  <th style={{ textAlign: "right" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.id || `${item.version}-${item.channel}`}>
                    <td>
                      <strong className="mono">{item.version}</strong>
                      {item.is_mandatory && (
                        <span
                          style={{
                            marginLeft: "6px",
                            fontSize: "11px",
                            padding: "2px 6px",
                            borderRadius: "4px",
                            background: "#ef4444",
                            color: "#fff",
                          }}
                        >
                          强制
                        </span>
                      )}
                    </td>
                    <td>{item.channel}</td>
                    <td>{formatSize(item.file_size)}</td>
                    <td style={{ maxWidth: "240px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={item.release_notes}>
                      {item.release_notes || "—"}
                    </td>
                    <td>{formatDate(item.created_at)}</td>
                    <td>
                      {item.file_exists ? (
                        <span style={{ color: "#10b981", fontWeight: 500 }}>
                          ● 在库可用
                        </span>
                      ) : item.package_deleted ? (
                        <span style={{ color: "#6b7280" }}>
                          已清理 (已释放)
                        </span>
                      ) : (
                        <span style={{ color: "#f59e0b" }}>
                          文件丢失
                        </span>
                      )}
                    </td>
                    <td style={{ textAlign: "right" }}>
                      <div style={{ display: "inline-flex", gap: "8px", justifyContent: "flex-end" }}>
                        {item.file_exists && item.download_url && (
                          <a
                            href={item.download_url}
                            className="button-link"
                            target="_blank"
                            rel="noreferrer"
                            style={{ padding: "4px 8px", fontSize: "12px" }}
                          >
                            下载
                          </a>
                        )}
                        {item.file_exists && (
                          <button
                            type="button"
                            className="danger"
                            style={{ padding: "4px 8px", fontSize: "12px" }}
                            onClick={() => setDeleteTarget(item)}
                          >
                            清理安装包
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 安装包清理确认弹窗 */}
      {deleteTarget && (
        <div
          className="release-modal-overlay"
          onClick={() => {
            if (!deleting) setDeleteTarget(null);
          }}
        >
          <div
            className="release-modal-card"
            onClick={(e) => e.stopPropagation()}
            role="dialog"
            aria-modal="true"
          >
            <div className="release-modal-header">
              <div className="release-modal-icon" aria-hidden="true">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 6h18" />
                  <path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6" />
                  <path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2" />
                  <line x1="10" y1="11" x2="10" y2="17" />
                  <line x1="14" y1="11" x2="14" y2="17" />
                </svg>
              </div>
              <div className="release-modal-title-box">
                <h3>确认清理安装包文件？</h3>
                <p>物理删除服务器历史包以释放存储空间</p>
              </div>
              <button
                type="button"
                className="release-modal-close"
                disabled={deleting}
                onClick={() => setDeleteTarget(null)}
                title="关闭"
              >
                ✕
              </button>
            </div>

            <div className="release-modal-body">
              <p style={{ margin: "0 0 14px", lineHeight: "1.6" }}>
                您确定要从服务器硬盘中物理删除版本{" "}
                <span className="release-modal-version-tag">
                  v{deleteTarget.version}
                </span>
                <span className="release-modal-channel-badge">
                  {deleteTarget.channel}
                </span>{" "}
                的升级安装包吗？
              </p>

              <div className="release-modal-info-box">
                <div className="release-modal-info-item">
                  <span style={{ color: "#059669", fontWeight: 600 }}>• 释放空间：</span>
                  <span>
                    将立即释放服务器约{" "}
                    <strong style={{ color: "#0f172a" }}>
                      {formatSize(deleteTarget.file_size)}
                    </strong>{" "}
                    物理硬盘空间
                  </span>
                </div>
                <div className="release-modal-info-item">
                  <span style={{ color: "#0284c7", fontWeight: 600 }}>• 数据存档：</span>
                  <span>该版本的更新日志及发布记录仍会永久留存展示</span>
                </div>
                <div className="release-modal-info-item">
                  <span style={{ color: "#e11d48", fontWeight: 600 }}>• 影响范围：</span>
                  <span>清理完成后客户端将无法再直接下载该历史版本的包</span>
                </div>
              </div>
            </div>

            <div className="release-modal-actions">
              <button
                type="button"
                className="release-btn-cancel"
                disabled={deleting}
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="release-btn-danger"
                disabled={deleting}
                onClick={() => void confirmDeletePackage()}
              >
                {deleting ? "清理中…" : "确认删除以释放空间"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
