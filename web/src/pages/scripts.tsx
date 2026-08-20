import { useEffect, useMemo, useState } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { javascript } from "@codemirror/lang-javascript";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiClient, ApiError, jsonBody } from "../api/client";
import type {
  Page,
  Profile,
  Script,
  ScriptVersion,
  Task,
  User,
  Workspace,
} from "../types";

const EXAMPLE_SOURCE = `module.exports.run = async ({ useBrowser, log, params }) => {
  log("script started");
  const url = "https://example.com";
  const timeoutMs = Number(params.timeoutMs) || 30000;
  const runtime = await useBrowser();
  const page = runtime.page;
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: timeoutMs });
  const title = await page.title();
  const finalUrl = page.url();
  log("script finished");
  return { success: true, title, url: finalUrl, params };
};`;

const EMPTY_SCHEMA = `{
  "type": "object",
  "properties": {},
  "additionalProperties": false
}`;

const fmt = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";
const messageOf = (error: unknown) =>
  error instanceof ApiError ? error.message : "请求失败，请稍后重试";
const stateName = (value: string) =>
  ({
    ENABLED: "已启用",
    DISABLED: "已禁用",
    PENDING: "等待中",
    DISPATCHED: "已派发",
    RUNNING: "运行中",
    SUCCESS: "成功",
    FAILED: "失败",
    TIMEOUT: "超时",
    CANCELLED: "已取消",
  })[value] || value;

function State({ value }: { value: string }) {
  return (
    <span className={`state state-${value.toLowerCase()}`}>
      {stateName(value)}
    </span>
  );
}

function Pager({
  page,
  pages,
  onChange,
}: {
  page: number;
  pages: number;
  onChange: (page: number) => void;
}) {
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <button disabled={page <= 1} onClick={() => onChange(page - 1)}>
        上一页
      </button>
      <span>{page} / {pages}</span>
      <button disabled={page >= pages} onClick={() => onChange(page + 1)}>
        下一页
      </button>
    </div>
  );
}

export function ScriptsPage({ user }: { user: User }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [data, setData] = useState<Page<Script> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const query = new URLSearchParams({
      paged: "true",
      page: String(page),
      page_size: "20",
      q,
      status,
    });
    apiClient<Page<Script>>(`/scripts?${query}`)
      .then(setData)
      .catch((exc) => setError(messageOf(exc)));
  }, [page, q, status]);
  return (
    <>
      <div className="page-title">
        <div>
          <h1>脚本中心</h1>
          <p className="muted">管理已登记的JavaScript脚本及不可变版本</p>
        </div>
        {user.role !== "MEMBER" && (
          <Link className="button-link primary" to="/scripts/new">
            新建脚本
          </Link>
        )}
      </div>
      <div className="toolbar">
        <input
          value={q}
          placeholder="搜索脚本名称或描述"
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
        />
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setPage(1);
          }}
        >
          <option value="">全部状态</option>
          <option value="ENABLED">已启用</option>
          <option value="DISABLED">已禁用</option>
        </select>
        <Link className="button-link" to="/script-runs">查看运行历史</Link>
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>脚本名称</th><th>描述</th><th>状态</th><th>当前版本</th>
              <th>创建人</th><th>更新时间</th>
            </tr>
          </thead>
          <tbody>
            {(data?.items || []).map((item) => (
              <tr key={item.script_id} className="clickable">
                <td><Link to={`/scripts/${item.script_id}`}>{item.name}</Link></td>
                <td>{item.description || "-"}</td>
                <td><State value={item.status} /></td>
                <td>Version {item.current_version}</td>
                <td>{item.created_by_username || item.created_by.slice(0, 8)}</td>
                <td>{fmt(item.updated_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!data?.items.length && <div className="empty">暂无脚本</div>}
      {data && <Pager page={data.page} pages={data.pages} onChange={setPage} />}
    </>
  );
}

type VersionDetail = ScriptVersion & { source: string };

export function ScriptEditorPage({ user }: { user: User }) {
  const { id = "new" } = useParams();
  const isNew = id === "new";
  const canEdit = user.role !== "MEMBER";
  const navigate = useNavigate();
  const [script, setScript] = useState<Script | null>(null);
  const [versions, setVersions] = useState<ScriptVersion[]>([]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [source, setSource] = useState(EXAMPLE_SOURCE);
  const [schemaText, setSchemaText] = useState(EMPTY_SCHEMA);
  const [selectedVersionId, setSelectedVersionId] = useState("");
  const [compareVersionId, setCompareVersionId] = useState("");
  const [compareSource, setCompareSource] = useState("");
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [selectedProfiles, setSelectedProfiles] = useState<string[]>([]);
  const [paramsText, setParamsText] = useState("{}");
  const [timeout, setTimeoutValue] = useState(30);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspaceId, setWorkspaceId] = useState(user.workspace_id || "");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    if (isNew) return;
    try {
      const [detail, history, profilePage] = await Promise.all([
        apiClient<Script>(`/scripts/${id}`),
        apiClient<ScriptVersion[]>(`/scripts/${id}/versions`),
        apiClient<Page<Profile>>("/profiles?paged=true&page=1&page_size=100"),
      ]);
      setScript(detail);
      setVersions(history);
      setName(detail.name);
      setDescription(detail.description);
      if (detail.current_version_detail) {
        setSource(detail.current_version_detail.source || "");
        setSchemaText(JSON.stringify(detail.current_version_detail.params_schema || {}, null, 2));
        setSelectedVersionId(detail.current_version_detail.script_version_id);
      }
      setProfiles(profilePage.items.filter((profile) => profile.workspace_id === detail.workspace_id));
    } catch (exc) {
      setError(messageOf(exc));
    }
  };
  useEffect(() => { void load(); }, [id]);
  useEffect(() => {
    if (!isNew || user.role !== "ADMIN") return;
    apiClient<Page<Workspace>>("/workspaces?paged=true&page=1&page_size=100")
      .then((page) => {
        setWorkspaces(page.items);
        setWorkspaceId((current) => current || page.items[0]?.workspace_id || "");
      })
      .catch((exc) => setError(messageOf(exc)));
  }, [isNew, user.role]);

  const parsed = (text: string, label: string) => {
    const value: unknown = JSON.parse(text);
    if (!value || Array.isArray(value) || typeof value !== "object")
      throw new Error(`${label}必须是JSON对象`);
    return value as Record<string, unknown>;
  };
  const save = async () => {
    if (!canEdit) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const params_schema = parsed(schemaText, "参数定义");
      if (isNew) {
        const created = await apiClient<Script>("/scripts", jsonBody({
          name, description, language: "javascript", source, params_schema,
          workspace_id: workspaceId || null,
        }));
        navigate(`/scripts/${created.script_id}`, { replace: true });
      } else {
        await apiClient(`/scripts/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ name, description }),
        });
        await apiClient(`/scripts/${id}/versions`, jsonBody({ source, params_schema }));
        setMessage("已保存为新的不可变版本");
        await load();
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : messageOf(exc));
    } finally { setBusy(false); }
  };
  const selectVersion = async (versionId: string) => {
    setSelectedVersionId(versionId);
    if (!id || isNew) return;
    try {
      const version = await apiClient<VersionDetail>(`/scripts/${id}/versions/${versionId}`);
      setSource(version.source);
      setSchemaText(JSON.stringify(version.params_schema || {}, null, 2));
    } catch (exc) { setError(messageOf(exc)); }
  };
  const compare = async (versionId: string) => {
    setCompareVersionId(versionId);
    if (!versionId || isNew) { setCompareSource(""); return; }
    try {
      const version = await apiClient<VersionDetail>(`/scripts/${id}/versions/${versionId}`);
      setCompareSource(version.source);
    } catch (exc) { setError(messageOf(exc)); }
  };
  const rollback = async () => {
    if (!canEdit || !selectedVersionId || isNew) return;
    if (!window.confirm("回滚会复制所选版本并创建一个新版本，是否继续？")) return;
    try {
      await apiClient(`/scripts/${id}/versions/${selectedVersionId}/rollback`, jsonBody({}));
      setMessage("回滚完成，已创建新版本");
      await load();
    } catch (exc) { setError(messageOf(exc)); }
  };
  const toggle = async () => {
    if (!script || !canEdit) return;
    try {
      const next = script.status === "ENABLED" ? "DISABLED" : "ENABLED";
      await apiClient(`/scripts/${id}`, { method: "PATCH", body: JSON.stringify({ status: next }) });
      await load();
    } catch (exc) { setError(messageOf(exc)); }
  };
  const run = async () => {
    if (!script) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const params = parsed(paramsText, "运行参数");
      const result = await apiClient<{ count: number; items: Task[] }>(
        `/scripts/${id}/execute`,
        jsonBody({
          script_version_id: selectedVersionId || null,
          profile_ids: selectedProfiles,
          params,
          timeout,
        }),
      );
      setMessage(`已为 ${result.count} 个Profile创建独立任务`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : messageOf(exc));
    } finally { setBusy(false); }
  };

  return (
    <>
      <div className="page-title">
        <div>
          <h1>{isNew ? "新建脚本" : script?.name || "脚本详情"}</h1>
          <p className="muted">仅通过Server任务下发到Windows Agent和Laogu Automation Hook执行</p>
        </div>
        <div className="toolbar compact">
          <Link className="button-link" to="/scripts">返回列表</Link>
          {!isNew && <Link className="button-link" to="/script-runs">运行历史</Link>}
        </div>
      </div>
      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}
      <section className="panel">
        <div className="form-grid script-meta-grid">
          <label>脚本名称<input value={name} disabled={!canEdit} onChange={(e) => setName(e.target.value)} /></label>
          <label>语言<input value="JavaScript" disabled /></label>
          {isNew && user.role === "ADMIN" && <label>工作区<select value={workspaceId} onChange={(e) => setWorkspaceId(e.target.value)}>{workspaces.map((workspace) => <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name}</option>)}</select></label>}
          {!isNew && <label>状态<div><State value={script?.status || "DISABLED"} /></div></label>}
          {!isNew && <label>当前版本<input value={`Version ${script?.current_version || "-"}`} disabled /></label>}
        </div>
        <label>描述<textarea value={description} disabled={!canEdit} onChange={(e) => setDescription(e.target.value)} /></label>
      </section>
      {!isNew && (
        <section className="panel version-toolbar">
          <label>查看/编辑基础版本
            <select value={selectedVersionId} onChange={(e) => void selectVersion(e.target.value)}>
              {versions.map((v) => <option key={v.script_version_id} value={v.script_version_id}>Version {v.version} · {fmt(v.created_at)}</option>)}
            </select>
          </label>
          <label>比较版本
            <select value={compareVersionId} onChange={(e) => void compare(e.target.value)}>
              <option value="">不比较</option>
              {versions.filter((v) => v.script_version_id !== selectedVersionId).map((v) => <option key={v.script_version_id} value={v.script_version_id}>Version {v.version}</option>)}
            </select>
          </label>
          {canEdit && <button onClick={() => void rollback()}>回滚所选版本</button>}
          {canEdit && <button onClick={() => void toggle()}>{script?.status === "ENABLED" ? "禁用脚本" : "启用脚本"}</button>}
        </section>
      )}
      <div className={compareSource ? "editor-grid" : ""}>
        <section className="panel editor-panel">
          <h2>JavaScript脚本</h2>
          <CodeMirror value={source} height="430px" extensions={[javascript()]} editable={canEdit} onChange={setSource} />
        </section>
        {compareSource && (
          <section className="panel editor-panel">
            <h2>比较版本（只读）</h2>
            <CodeMirror value={compareSource} height="430px" extensions={[javascript()]} editable={false} />
          </section>
        )}
      </div>
      <section className="panel">
        <h2>参数定义（params_schema）</h2>
        <textarea className="code-textarea" value={schemaText} disabled={!canEdit} onChange={(e) => setSchemaText(e.target.value)} />
        {canEdit && <button className="primary" disabled={busy || !name.trim() || (isNew && user.role === "ADMIN" && !workspaceId)} onClick={() => void save()}>{isNew ? "创建脚本（Version 1，默认禁用）" : "保存为新Version"}</button>}
      </section>
      {!isNew && (
        <section className="panel">
          <h2>运行脚本</h2>
          {script?.status !== "ENABLED" && <div className="alert error">脚本处于禁用状态，启用后才能创建任务。</div>}
          <div className="profile-picker">
            {profiles.map((profile) => {
              const key = `${profile.agent_id}:${profile.profile_id}`;
              return (
                <label className="check-row" key={key}>
                  <input type="checkbox" checked={selectedProfiles.includes(profile.profile_id)} onChange={(e) => setSelectedProfiles(e.target.checked ? [...selectedProfiles, profile.profile_id] : selectedProfiles.filter((p) => p !== profile.profile_id))} />
                  Profile {profile.profile_id} · {profile.x_username || "未识别账号"} · {profile.browser_status}
                </label>
              );
            })}
          </div>
          {!profiles.length && <div className="empty">当前工作区没有可用Profile</div>}
          <div className="run-grid">
            <label>运行参数（JSON对象）<textarea className="code-textarea small" value={paramsText} onChange={(e) => setParamsText(e.target.value)} /></label>
            <label>超时（秒）<input type="number" min={1} max={300} value={timeout} onChange={(e) => setTimeoutValue(Number(e.target.value))} /></label>
          </div>
          <button className="primary" disabled={busy || script?.status !== "ENABLED" || !selectedProfiles.length} onClick={() => void run()}>为所选Profile创建独立任务</button>
        </section>
      )}
    </>
  );
}

export function ScriptRunsPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [profileId, setProfileId] = useState("");
  const [data, setData] = useState<Page<Task> | null>(null);
  const [selected, setSelected] = useState<Task | null>(null);
  const [error, setError] = useState("");
  const params = useMemo(() => new URLSearchParams({ page: String(page), page_size: "20", q, status, profile_id: profileId }).toString(), [page, q, status, profileId]);
  const load = () => apiClient<Page<Task>>(`/script-runs?${params}`).then(setData).catch((exc) => setError(messageOf(exc)));
  useEffect(() => { void load(); }, [params]);
  const detail = async (taskId: string) => {
    try { setSelected(await apiClient<Task>(`/tasks/${taskId}`)); }
    catch (exc) { setError(messageOf(exc)); }
  };
  const cancel = async () => {
    if (!selected) return;
    try {
      const result = await apiClient<Task>(`/tasks/${selected.task_id}/cancel`, jsonBody({}));
      setSelected(result); await load();
    } catch (exc) { setError(messageOf(exc)); }
  };
  return (
    <>
      <div className="page-title"><div><h1>脚本运行历史</h1><p className="muted">每个Profile对应独立任务、状态、日志和结果</p></div><Link className="button-link" to="/scripts">返回脚本中心</Link></div>
      <div className="toolbar">
        <input value={q} placeholder="搜索脚本、任务ID或Profile" onChange={(e) => { setQ(e.target.value); setPage(1); }} />
        <input value={profileId} placeholder="Profile ID" onChange={(e) => { setProfileId(e.target.value); setPage(1); }} />
        <select value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}><option value="">全部状态</option>{["PENDING", "DISPATCHED", "RUNNING", "SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"].map((s) => <option value={s} key={s}>{stateName(s)}</option>)}</select>
      </div>
      {error && <div className="alert error">{error}</div>}
      <div className="table-wrap"><table><thead><tr><th>脚本</th><th>Version</th><th>Profile</th><th>账号</th><th>Agent</th><th>状态</th><th>耗时</th><th>创建/开始/结束</th></tr></thead><tbody>
        {(data?.items || []).map((item) => <tr key={item.task_id} className="clickable" onClick={() => void detail(item.task_id)}><td>{item.script_name || item.script_id || "-"}<div className="mono">{item.task_id.slice(0, 12)}…</div></td><td>{item.script_version || "-"}</td><td>{item.profile_id}</td><td>{item.x_account_id || "-"}</td><td className="mono">{item.agent_id.slice(0, 10)}…</td><td><State value={item.status} /></td><td>{item.duration?.toFixed(3) || "0.000"}s</td><td>{fmt(item.created_at)}<br />{fmt(item.started_at)}<br />{fmt(item.finished_at)}</td></tr>)}
      </tbody></table></div>
      {!data?.items.length && <div className="empty">暂无脚本运行记录</div>}
      {data && <Pager page={data.page} pages={data.pages} onChange={setPage} />}
      {selected && <section className="panel detail"><div className="page-title"><div><h2>任务详情</h2><span className="mono">{selected.task_id}</span></div><button onClick={() => setSelected(null)}>关闭</button></div><p>Profile：{selected.profile_id}　状态：<State value={selected.status} /></p>{!["SUCCESS", "FAILED", "TIMEOUT", "CANCELLED"].includes(selected.status) && <button onClick={() => void cancel()}>停止任务</button>}<h2>参数</h2><pre className="json-view">{JSON.stringify(selected.params || {}, null, 2)}</pre><h2>执行日志</h2><pre className="log-view">{selected.activity?.logs?.join("\n") || "暂无日志"}</pre><h2>结果 / 错误</h2><pre className="json-view">{JSON.stringify(selected.result ?? selected.error ?? null, null, 2)}</pre></section>}
    </>
  );
}
