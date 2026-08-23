import { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { Page, Workspace } from "../types";

type Binding = { configured: boolean; workspace_id: string; enabled: boolean; bot_token_last4: string; admin_telegram_user_id: string; default_target_language: string; last_error: string; last_poll_at: string | null };
type AllowedUser = { id: string; telegram_user_id: string; username: string; display_name: string; enabled: boolean; created_at: string };
const languages = [["zh-ja-auto", "中日双向自动识别"], ["zh-CN", "简体中文"], ["zh-TW", "繁体中文"], ["en", "English"], ["ja", "日本語"], ["ko", "한국어"], ["fr", "Français"], ["de", "Deutsch"], ["es", "Español"], ["ru", "Русский"], ["pt-BR", "Português (Brasil)"]] as const;
const errorMessage = (error: unknown) => error instanceof ApiError ? error.message : "请求失败，请稍后重试";

export function TelegramTranslationPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [binding, setBinding] = useState<Binding | null>(null);
  const [allowedUsers, setAllowedUsers] = useState<AllowedUser[]>([]);
  const [token, setToken] = useState("");
  const [adminId, setAdminId] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("zh-CN");
  const [enabled, setEnabled] = useState(false);
  const [newUser, setNewUser] = useState({ telegram_user_id: "", username: "", display_name: "" });
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient<Page<Workspace>>("/workspaces?paged=true&page=1&page_size=100").then((result) => {
      setWorkspaces(result.items);
      setWorkspaceId(result.items[0]?.workspace_id || "");
    }).catch((reason) => setError(errorMessage(reason)));
  }, []);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const [nextBinding, users] = await Promise.all([
        apiClient<Binding>(`/admin/telegram/translation?workspace_id=${encodeURIComponent(workspaceId)}`),
        apiClient<AllowedUser[]>(`/admin/telegram/translation/users?workspace_id=${encodeURIComponent(workspaceId)}`),
      ]);
      setBinding(nextBinding); setAllowedUsers(users); setAdminId(nextBinding.admin_telegram_user_id || "");
      setTargetLanguage(nextBinding.default_target_language || "zh-CN"); setEnabled(nextBinding.enabled); setToken("");
    } catch (reason) { setError(errorMessage(reason)); }
  }, [workspaceId]);
  useEffect(() => { void load(); }, [load]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy("save"); setError(""); setMessage("");
    try {
      await apiClient("/admin/telegram/translation", { method: "PUT", body: JSON.stringify({ workspace_id: workspaceId, bot_token: token || undefined, admin_telegram_user_id: adminId, default_target_language: targetLanguage, enabled }) });
      setMessage("机器人配置已保存，管理员已自动加入授权名单。"); await load();
    } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(""); }
  };
  const test = async () => {
    if (!token) { setError("测试时需要填写 Bot Token"); return; }
    setBusy("test"); setError("");
    try {
      const result = await apiClient<{ bot: { username: string; name: string } }>("/admin/telegram/translation/test", { method: "POST", body: JSON.stringify({ bot_token: token }) });
      setMessage(`连接成功：@${result.bot.username || result.bot.name}`);
    } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(""); }
  };
  const addUser = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy("add"); setError("");
    try {
      await apiClient(`/admin/telegram/translation/users?workspace_id=${encodeURIComponent(workspaceId)}`, { method: "POST", body: JSON.stringify(newUser) });
      setNewUser({ telegram_user_id: "", username: "", display_name: "" }); setMessage("Telegram 用户已授权。"); await load();
    } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(""); }
  };
  const remove = async (item: AllowedUser) => {
    if (!window.confirm(`确认取消用户 ${item.telegram_user_id} 的权限？`)) return;
    setBusy(item.id);
    try {
      await apiClient(`/admin/telegram/translation/users/${encodeURIComponent(item.telegram_user_id)}?workspace_id=${encodeURIComponent(workspaceId)}`, { method: "DELETE" });
      setMessage("授权已取消。"); await load();
    } catch (reason) { setError(errorMessage(reason)); } finally { setBusy(""); }
  };
  return <>
    <div className="page-title"><div><h1>Telegram 翻译机器人</h1><p className="muted">绑定机器人，并指定允许使用 AI 翻译的 Telegram 用户。</p></div></div>
    <div className="alert">Bot Token 仅在服务器加密保存，页面不会显示明文。Telegram 用户看不到 AI 密钥、Provider 或模型信息。</div>
    {error && <div className="alert error">{error}</div>}{message && <div className="alert">{message}</div>}
    <form className="panel" onSubmit={save}><h2>机器人绑定</h2><div className="form-grid">
      <label>工作区<select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>{workspaces.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}</select></label>
      <label>Bot Token<input type="password" autoComplete="new-password" value={token} onChange={(event) => setToken(event.target.value)} placeholder={binding?.configured ? `已配置，末四位 ${binding.bot_token_last4}；留空保持不变` : "从 BotFather 获取"} required={!binding?.configured} /></label>
      <label>管理员 Telegram 用户 ID<input value={adminId} onChange={(event) => setAdminId(event.target.value)} inputMode="numeric" required /></label>
      <label>默认目标语言<select value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)}>{languages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="checkbox-row"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用机器人翻译服务</label>
    </div><div className="modal-actions"><button type="button" onClick={() => void test()} disabled={busy === "test"}>测试 Bot Token</button><button className="primary" disabled={busy === "save"}>保存配置</button></div>
    <p className="muted">最近轮询：{binding?.last_poll_at ? new Date(binding.last_poll_at).toLocaleString("zh-CN") : "-"}{binding?.last_error ? ` · 异常：${binding.last_error}` : ""}</p></form>
    <form className="panel" onSubmit={addUser}><h2>新增授权用户</h2><div className="form-grid">
      <label>Telegram 用户 ID<input value={newUser.telegram_user_id} onChange={(event) => setNewUser({ ...newUser, telegram_user_id: event.target.value })} inputMode="numeric" required /></label>
      <label>用户名<input value={newUser.username} onChange={(event) => setNewUser({ ...newUser, username: event.target.value })} placeholder="可选，不含 @" /></label>
      <label>备注<input value={newUser.display_name} onChange={(event) => setNewUser({ ...newUser, display_name: event.target.value })} /></label>
    </div><button className="primary" disabled={busy === "add" || !binding?.configured}>授权用户</button></form>
    <section className="panel"><h2>已授权用户</h2>{allowedUsers.length ? <div className="table-wrap"><table><thead><tr><th>用户 ID</th><th>用户名</th><th>备注</th><th>操作</th></tr></thead><tbody>{allowedUsers.map((item) => <tr key={item.id}><td className="mono">{item.telegram_user_id}</td><td>{item.username ? `@${item.username}` : "-"}</td><td>{item.display_name || "-"}</td><td><button className="danger-button" onClick={() => void remove(item)} disabled={busy === item.id}>取消授权</button></td></tr>)}</tbody></table></div> : <div className="empty">暂无授权用户</div>}</section>
    <section className="panel"><h2>使用方法</h2><p>选择“中日双向自动识别”后，中文会自动翻译成日语，日语会自动翻译成中文；<code>/to en 文本</code> 可临时指定其他目标语言。</p></section>
  </>;
}
