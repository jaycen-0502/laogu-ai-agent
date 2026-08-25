import { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { Page, Workspace } from "../types";


type Binding = {
  configured: boolean;
  binding_id?: string;
  workspace_id: string;
  enabled: boolean;
  bot_token_last4: string;
  admin_telegram_user_id: string;
  default_target_language: string;
  last_error: string;
  last_poll_at: string | null;
};

type AllowedUser = {
  id: string;
  telegram_user_id: string;
  username: string;
  display_name: string;
  enabled: boolean;
  created_at: string;
};

const languages = [
  ["zh-CN", "简体中文"],
  ["zh-TW", "繁体中文"],
  ["en", "English"],
  ["ja", "日本語"],
  ["ko", "한국어"],
  ["fr", "Français"],
  ["de", "Deutsch"],
  ["es", "Español"],
  ["ru", "Русский"],
  ["pt-BR", "Português (Brasil)"],
] as const;

const errorMessage = (error: unknown) =>
  error instanceof ApiError ? error.message : "请求失败，请稍后重试";

export function TelegramTranslationPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState("");
  const [binding, setBinding] = useState<Binding | null>(null);
  const [allowedUsers, setAllowedUsers] = useState<AllowedUser[]>([]);
  const [token, setToken] = useState("");
  const [adminId, setAdminId] = useState("");
  const [targetLanguage, setTargetLanguage] = useState("zh-CN");
  const [enabled, setEnabled] = useState(false);
  const [telegramUserId, setTelegramUserId] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    apiClient<Page<Workspace>>("/workspaces?paged=true&page=1&page_size=100")
      .then((result) => {
        setWorkspaces(result.items);
        setWorkspaceId((current) => current || result.items[0]?.workspace_id || "");
      })
      .catch((reason) => setError(errorMessage(reason)));
  }, []);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    setError("");
    try {
      const [nextBinding, nextUsers] = await Promise.all([
        apiClient<Binding>(
          `/admin/telegram/translation?workspace_id=${encodeURIComponent(workspaceId)}`,
        ),
        apiClient<AllowedUser[]>(
          `/admin/telegram/translation/users?workspace_id=${encodeURIComponent(workspaceId)}`,
        ),
      ]);
      setBinding(nextBinding);
      setAllowedUsers(nextUsers);
      setAdminId(nextBinding.admin_telegram_user_id || "");
      setTargetLanguage(nextBinding.default_target_language || "zh-CN");
      setEnabled(Boolean(nextBinding.enabled));
      setToken("");
    } catch (reason) {
      setError(errorMessage(reason));
    }
  }, [workspaceId]);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setError("");
    setMessage("");
    try {
      const payload: Record<string, unknown> = {
        workspace_id: workspaceId,
        admin_telegram_user_id: adminId,
        default_target_language: targetLanguage,
        enabled,
      };
      if (token) payload.bot_token = token;
      await apiClient("/admin/telegram/translation", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setMessage("Telegram 机器人配置已保存，管理员已自动加入授权名单。");
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy("");
    }
  };

  const testToken = async () => {
    if (!token) {
      setError("测试时需要填写 Bot Token");
      return;
    }
    setBusy("test");
    setError("");
    setMessage("");
    try {
      const result = await apiClient<{
        ok: boolean;
        bot: { username: string; name: string };
      }>("/admin/telegram/translation/test", {
        method: "POST",
        body: JSON.stringify({ bot_token: token }),
      });
      setMessage(`连接成功：@${result.bot.username || result.bot.name}`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy("");
    }
  };

  const addUser = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("add");
    setError("");
    try {
      await apiClient(
        `/admin/telegram/translation/users?workspace_id=${encodeURIComponent(workspaceId)}`,
        {
          method: "POST",
          body: JSON.stringify({
            telegram_user_id: telegramUserId,
            username,
            display_name: displayName,
          }),
        },
      );
      setTelegramUserId("");
      setUsername("");
      setDisplayName("");
      setMessage("Telegram 用户已授权。");
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy("");
    }
  };

  const removeUser = async (item: AllowedUser) => {
    if (!window.confirm(`确认取消 Telegram 用户 ${item.telegram_user_id} 的翻译权限？`)) return;
    setBusy(`remove:${item.id}`);
    setError("");
    try {
      await apiClient(
        `/admin/telegram/translation/users/${encodeURIComponent(item.telegram_user_id)}?workspace_id=${encodeURIComponent(workspaceId)}`,
        { method: "DELETE" },
      );
      setMessage("Telegram 用户授权已取消。");
      await load();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy("");
    }
  };

  return (
    <>
      <div className="page-title">
        <div>
          <h1>Telegram 翻译机器人</h1>
          <p className="muted">绑定机器人，并指定允许使用 AI 翻译的 Telegram 用户。</p>
        </div>
      </div>
      <div className="alert">
        Bot Token 仅在服务器加密保存，页面不会显示明文。翻译内容不会写入日志，Telegram 用户也看不到 AI 密钥、Provider 或模型信息。
      </div>
      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}

      <form className="panel" onSubmit={save}>
        <h2>机器人绑定</h2>
        <div className="form-grid">
          <label>
            工作区
            <select value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)} required>
              {workspaces.map((workspace) => (
                <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name}</option>
              ))}
            </select>
          </label>
          <label>
            Bot Token
            <input
              type="password"
              autoComplete="new-password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              placeholder={binding?.configured ? `已配置，末四位 ${binding.bot_token_last4}；留空保持不变` : "从 BotFather 获取"}
              required={!binding?.configured}
            />
          </label>
          <label>
            管理员 Telegram 用户 ID
            <input value={adminId} onChange={(event) => setAdminId(event.target.value)} inputMode="numeric" required />
          </label>
          <label>
            默认翻译目标语言
            <select value={targetLanguage} onChange={(event) => setTargetLanguage(event.target.value)}>
              {languages.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="checkbox-row">
            <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
            启用机器人轮询与翻译服务
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" onClick={() => void testToken()} disabled={busy === "test"}>
            {busy === "test" ? "测试中…" : "测试 Bot Token"}
          </button>
          <button className="primary" disabled={busy === "save"}>
            {busy === "save" ? "保存中…" : "保存配置"}
          </button>
        </div>
        <p className="muted">
          最近轮询：{binding?.last_poll_at ? new Date(binding.last_poll_at).toLocaleString("zh-CN") : "-"}
          {binding?.last_error ? ` · 异常：${binding.last_error}` : ""}
        </p>
      </form>

      <form className="panel" onSubmit={addUser}>
        <h2>新增授权用户</h2>
        <div className="form-grid">
          <label>
            Telegram 用户 ID
            <input value={telegramUserId} onChange={(event) => setTelegramUserId(event.target.value)} inputMode="numeric" required />
          </label>
          <label>
            Telegram 用户名
            <input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="可选，不含 @" />
          </label>
          <label>
            备注名称
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="可选" />
          </label>
        </div>
        <button className="primary" disabled={busy === "add" || !binding?.configured}>
          {busy === "add" ? "添加中…" : "授权用户"}
        </button>
      </form>

      <section className="panel">
        <h2>已授权 Telegram 用户</h2>
        {allowedUsers.length ? (
          <div className="table-wrap">
            <table>
              <thead><tr><th>用户 ID</th><th>用户名</th><th>备注</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                {allowedUsers.map((item) => (
                  <tr key={item.id}>
                    <td className="mono">{item.telegram_user_id}</td>
                    <td>{item.username ? `@${item.username}` : "-"}</td>
                    <td>{item.display_name || "-"}</td>
                    <td>{item.enabled ? "已授权" : "已停用"}</td>
                    <td><button className="danger-button" onClick={() => void removeUser(item)} disabled={busy === `remove:${item.id}`}>取消授权</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="empty">暂无授权用户</div>}
      </section>

      <section className="panel">
        <h2>机器人使用方法</h2>
        <p>直接向机器人发送文本，使用管理员设置的默认目标语言翻译。</p>
        <p><code>/to en 需要翻译的文本</code> 可临时指定目标语言，支持 en、ja、ko、fr、de、es、ru、zh-CN 等。</p>
      </section>
    </>
  );
}
