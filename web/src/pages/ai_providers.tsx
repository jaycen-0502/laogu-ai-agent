import { useCallback, useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { AIProvider, Page, User, Workspace } from "../types";


const messageOf = (error: unknown) =>
  error instanceof ApiError ? error.message : "请求失败，请稍后重试";

const fmt = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";

const stateName = (value: string) =>
  ({
    ENABLED: "已启用",
    DISABLED: "已禁用",
    SUCCESS: "成功",
    FAILED: "失败",
    UNKNOWN: "未测试",
  })[value] || value;

function State({ value }: { value: string }) {
  return <span className={`state state-${value.toLowerCase()}`}>{stateName(value)}</span>;
}

type FormState = {
  name: string;
  provider_type: "OPENAI" | "OPENAI_COMPATIBLE";
  base_url: string;
  api_key: string;
  default_model: string;
  status: "ENABLED" | "DISABLED";
  is_default: boolean;
  workspace_id: string;
};

const emptyForm = (workspaceId = ""): FormState => ({
  name: "",
  provider_type: "OPENAI",
  base_url: "",
  api_key: "",
  default_model: "",
  status: "DISABLED",
  is_default: false,
  workspace_id: workspaceId,
});

export function AIProvidersPage({ user }: { user: User }) {
  const canManage = user.role !== "MEMBER";
  const [data, setData] = useState<Page<AIProvider> | null>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [editingId, setEditingId] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(emptyForm(user.workspace_id || ""));
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const query = new URLSearchParams({
      paged: "true",
      page: String(page),
      page_size: "20",
      q,
      status: statusFilter,
    });
    setError("");
    try {
      setData(await apiClient<Page<AIProvider>>(`/ai/providers?${query}`));
    } catch (exc) {
      setError(messageOf(exc));
    }
  }, [page, q, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (user.role !== "ADMIN") return;
    apiClient<Page<Workspace>>("/workspaces?paged=true&page=1&page_size=100")
      .then((result) => setWorkspaces(result.items))
      .catch(() => setWorkspaces([]));
  }, [user.role]);

  const startCreate = () => {
    setEditingId("");
    setForm(emptyForm(user.workspace_id || workspaces[0]?.workspace_id || ""));
    setMessage("");
    setError("");
    setShowForm(true);
  };

  const startEdit = (item: AIProvider) => {
    setEditingId(item.provider_id);
    setForm({
      name: item.name,
      provider_type: item.provider_type,
      base_url: item.base_url,
      api_key: "",
      default_model: item.default_model,
      status: item.status,
      is_default: item.is_default,
      workspace_id: item.workspace_id,
    });
    setMessage("");
    setError("");
    setShowForm(true);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy("save");
    setError("");
    setMessage("");
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        provider_type: form.provider_type,
        base_url: form.provider_type === "OPENAI" && !form.base_url.trim() ? "" : form.base_url,
        default_model: form.default_model,
        status: form.status,
        is_default: form.is_default,
      };
      if (form.api_key) payload.api_key = form.api_key;
      if (!editingId && user.role === "ADMIN") payload.workspace_id = form.workspace_id;
      if (!editingId && !form.api_key) throw new ApiError(422, "新建Provider必须填写API Key");
      await apiClient<AIProvider>(editingId ? `/ai/providers/${editingId}` : "/ai/providers", {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      setMessage(editingId ? "Provider已更新" : "Provider已创建");
      setShowForm(false);
      await load();
    } catch (exc) {
      setError(messageOf(exc));
    } finally {
      setBusy("");
    }
  };

  const testConnection = async (item: AIProvider) => {
    setBusy(`test:${item.provider_id}`);
    setError("");
    setMessage("");
    try {
      const result = await apiClient<{ status: string; models: string[]; error?: string }>(
        `/ai/providers/${item.provider_id}/test`,
        { method: "POST" },
      );
      setMessage(result.status === "SUCCESS"
        ? `连接成功，发现 ${result.models.length} 个模型`
        : `连接失败：${result.error || "未知错误"}`);
      await load();
    } catch (exc) {
      setError(messageOf(exc));
    } finally {
      setBusy("");
    }
  };

  const remove = async (item: AIProvider) => {
    if (!window.confirm(`确定删除Provider“${item.name}”吗？`)) return;
    setBusy(`delete:${item.provider_id}`);
    setError("");
    try {
      await apiClient(`/ai/providers/${item.provider_id}`, { method: "DELETE" });
      setMessage("Provider已删除");
      await load();
    } catch (exc) {
      setError(messageOf(exc));
    } finally {
      setBusy("");
    }
  };

  return (
    <>
      <div className="page-title">
        <div>
          <h1>AI Provider</h1>
          <p className="muted">管理AI服务地址、加密凭据、默认模型和连接状态</p>
        </div>
        {canManage && <button className="primary" onClick={startCreate}>新增Provider</button>}
      </div>

      <div className="alert">
        API Key只在服务端加密保存，页面不会显示或返回完整密钥。连接测试优先读取模型列表；兼容站不支持列表时会检查Responses接口，不发送聊天内容。
      </div>
      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}

      {showForm && canManage && (
        <form className="panel" onSubmit={save}>
          <h2>{editingId ? "编辑Provider" : "新增Provider"}</h2>
          <div className="form-grid provider-form-grid">
            <label>
              名称
              <input required value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
            </label>
            <label>
              类型
              <select value={form.provider_type} onChange={(event) => setForm({ ...form, provider_type: event.target.value as FormState["provider_type"] })}>
                <option value="OPENAI">OpenAI</option>
                <option value="OPENAI_COMPATIBLE">OpenAI兼容接口</option>
              </select>
            </label>
            {user.role === "ADMIN" && !editingId && (
              <label>
                工作区
                <select required value={form.workspace_id} onChange={(event) => setForm({ ...form, workspace_id: event.target.value })}>
                  <option value="">请选择工作区</option>
                  {workspaces.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}
                </select>
              </label>
            )}
            <label>
              状态
              <select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as FormState["status"] })}>
                <option value="DISABLED">禁用</option>
                <option value="ENABLED">启用</option>
              </select>
            </label>
            <label>
              API Base URL
              <input
                value={form.base_url}
                placeholder={form.provider_type === "OPENAI" ? "留空使用 https://api.openai.com/v1" : "https://provider.example/v1"}
                required={form.provider_type === "OPENAI_COMPATIBLE"}
                onChange={(event) => setForm({ ...form, base_url: event.target.value })}
              />
            </label>
            <label>
              API Key
              <input
                type="password"
                autoComplete="new-password"
                value={form.api_key}
                required={!editingId}
                placeholder={editingId ? "留空表示保持原密钥" : "请输入API Key"}
                onChange={(event) => setForm({ ...form, api_key: event.target.value })}
              />
            </label>
            <label>
              默认模型
              <input
                value={form.default_model}
                placeholder="例如 gpt-5.4"
                list="provider-models"
                onChange={(event) => setForm({ ...form, default_model: event.target.value })}
              />
            </label>
            <label className="check-row provider-default-check">
              <input
                type="checkbox"
                checked={form.is_default}
                disabled={form.status !== "ENABLED"}
                onChange={(event) => setForm({ ...form, is_default: event.target.checked })}
              />
              设为工作区默认Provider
            </label>
          </div>
          <div className="toolbar compact">
            <button className="primary" disabled={busy === "save"}>{busy === "save" ? "保存中…" : "保存"}</button>
            <button type="button" onClick={() => setShowForm(false)}>取消</button>
          </div>
        </form>
      )}

      <div className="toolbar">
        <input value={q} placeholder="搜索名称或默认模型" onChange={(event) => { setQ(event.target.value); setPage(1); }} />
        <select value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(1); }}>
          <option value="">全部状态</option>
          <option value="ENABLED">已启用</option>
          <option value="DISABLED">已禁用</option>
        </select>
      </div>

      <datalist id="provider-models">
        {Array.from(new Set((data?.items || []).flatMap((item) => item.models))).map((model) => <option key={model} value={model} />)}
      </datalist>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>名称</th><th>类型</th><th>状态</th><th>默认</th><th>默认模型</th>
              <th>API Key</th><th>连接测试</th><th>最近测试</th>{canManage && <th>操作</th>}
            </tr>
          </thead>
          <tbody>
            {(data?.items || []).map((item) => (
              <tr key={item.provider_id}>
                <td><strong>{item.name}</strong><div className="muted mono">{item.base_url}</div></td>
                <td>{item.provider_type === "OPENAI" ? "OpenAI" : "OpenAI兼容"}</td>
                <td><State value={item.status} /></td>
                <td>{item.is_default ? "是" : "-"}</td>
                <td>{item.default_model || "-"}</td>
                <td className="mono">{item.api_key_masked || "-"}</td>
                <td>
                  <State value={item.last_test_status} />
                  {item.last_error && <div className="muted provider-error">{item.last_error}</div>}
                </td>
                <td>{fmt(item.last_tested_at)}<div className="muted">{item.models.length} 个模型</div></td>
                {canManage && (
                  <td>
                    <div className="provider-actions">
                      <button onClick={() => startEdit(item)}>编辑</button>
                      <button disabled={busy === `test:${item.provider_id}`} onClick={() => void testConnection(item)}>
                        {busy === `test:${item.provider_id}` ? "测试中…" : "测试连接"}
                      </button>
                      <button disabled={item.status === "ENABLED" || busy === `delete:${item.provider_id}`} onClick={() => void remove(item)}>删除</button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data?.items.length && <div className="empty">暂无AI Provider</div>}
      {data && data.pages > 1 && (
        <div className="pager">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button>
          <span>{page} / {data.pages}</span>
          <button disabled={page >= data.pages} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      )}
    </>
  );
}
