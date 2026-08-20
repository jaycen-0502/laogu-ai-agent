import { useEffect, useMemo, useState } from "react";
import { apiClient, authStore, ApiError, jsonBody } from "../api/client";
import type {
  Account,
  AIProvider,
  Activity,
  Agent,
  Audit,
  Dashboard,
  Invitation,
  Page,
  Profile,
  Task,
  User,
  Workspace,
} from "../types";

const fmt = (value?: string | null) =>
  value ? new Date(value).toLocaleString("zh-CN") : "-";
const taskTypeNames: Record<string, string> = {
  "browser.open_url": "打开网页",
  "x.check_login": "检查X登录状态",
  "x.read_profile": "读取X个人资料",
  "x.read_timeline": "读取X时间线",
  "x.search": "X关键词搜索",
};
const statusNames: Record<string, string> = {
  ACTIVE: "启用",
  DISABLED: "禁用",
  DELETED: "已删除",
  ONLINE: "在线",
  OFFLINE: "离线",
  RUNNING: "运行中",
  STOPPED: "已停止",
  PENDING: "等待中",
  SUCCESS: "成功",
  FAILED: "失败",
  TIMEOUT: "超时",
  CANCELLED: "已取消",
  LOGGED_IN: "已登录",
  NOT_LOGGED_IN: "未登录",
  UNKNOWN: "未知",
  VALID: "有效",
  DUPLICATE_ACCOUNT: "重复账号",
  ACCEPTED: "已接受",
  EXPIRED: "已过期",
  REVOKED: "已撤销",
};
const actionNames: Record<string, string> = {
  LOGIN: "登录",
  BOOTSTRAP: "初始化",
  WORKSPACE_CREATE: "创建工作区",
  WORKSPACE_UPDATE: "修改工作区",
  USER_CREATE: "创建用户",
  USER_UPDATE: "修改用户",
  AGENT_REGISTER: "注册运行端",
  AGENT_HEARTBEAT: "运行端心跳",
  AGENT_TOKEN_ROTATE: "轮换运行端令牌",
  AGENT_TOKEN_REVOKE: "吊销运行端令牌",
  ACCOUNT_SYNC: "同步账号",
  TASK_CREATE: "创建任务",
  TASK_CANCEL: "取消任务",
  TASK_RESULT: "提交任务结果",
};
const taskTypeLabel = (value: string) => taskTypeNames[value] || value;
const statusLabel = (value: string | null | undefined) =>
  statusNames[value || "UNKNOWN"] || value || "未知";
const actionLabel = (value: string) => actionNames[value] || taskTypeLabel(value);
const periodLabel = (value: string) =>
  ({ today: "今日", "7d": "近7天", "30d": "近30天", all: "全部" })[value] || value;
const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : "请求失败，请稍后重试";
const query = (values: Record<string, string | number | undefined>) =>
  new URLSearchParams(
    Object.entries(values)
      .filter(([, value]) => value !== undefined && value !== "")
      .map(([key, value]) => [key, String(value)]),
  ).toString();

function PageTitle({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {action}
    </div>
  );
}
function Card({
  label,
  value,
  tone = "",
}: {
  label: string;
  value: string | number;
  tone?: string;
}) {
  return (
    <div className={`metric-card ${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function State({ value }: { value: string | null | undefined }) {
  const text = value || "UNKNOWN";
  return (
    <span className={`state state-${text.toLowerCase()}`}>
      {statusLabel(text)}
    </span>
  );
}
function Empty({ text = "暂无数据" }) {
  return <div className="empty">{text}</div>;
}
function Table({ children }: { children: React.ReactNode }) {
  return (
    <div className="table-wrap">
      <table>{children}</table>
    </div>
  );
}
function PageNav({
  page,
  pages,
  onPage,
}: {
  page: number;
  pages: number;
  onPage: (page: number) => void;
}) {
  if (pages <= 1) return null;
  return (
    <div className="pager">
      <button disabled={page <= 1} onClick={() => onPage(page - 1)}>
        上一页
      </button>
      <span>
        {page} / {pages}
      </span>
      <button disabled={page >= pages} onClick={() => onPage(page + 1)}>
        下一页
      </button>
    </div>
  );
}
function usePage<T>(path: string, deps: unknown[] = []) {
  const [data, setData] = useState<Page<T>>({
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
    pages: 0,
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const reload = () => {
    setLoading(true);
    apiClient<Page<T>>(path)
      .then(setData)
      .catch((exc) => setError(errorText(exc)))
      .finally(() => setLoading(false));
  };
  useEffect(reload, [path, ...deps]);
  return { data, error, loading, reload, setData };
}

export function DashboardPage() {
  const [period, setPeriod] = useState("today");
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const load = () =>
    apiClient<Dashboard>(`/dashboard?period=${period}`)
      .then(setData)
      .catch((exc) => setError(errorText(exc)));
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 8000);
    return () => window.clearInterval(timer);
  }, [period]);
  if (error)
    return (
      <>
        <PageTitle title="控制台" />
        <div className="alert error">{error}</div>
      </>
    );
  if (!data) return <div className="loading">正在加载控制台…</div>;
  return (
    <>
      <PageTitle
        title="控制台"
        description={`数据范围：${periodLabel(period)}`}
        action={
          <select
            value={period}
            onChange={(event) => setPeriod(event.target.value)}
          >
            <option value="today">今日</option>
            <option value="7d">近7天</option>
            <option value="30d">近30天</option>
            <option value="all">全部</option>
          </select>
        }
      />
      <div className="metrics">
        <Card label="工作区" value={data.workspace_count} />
        <Card
          label="在线运行端"
          value={`${data.online_agents}/${data.agent_count}`}
          tone="good"
        />
        <Card label="浏览器环境" value={data.profile_count} />
        <Card label="已登录账号" value={data.logged_in_accounts} tone="good" />
        <Card label="运行中任务" value={data.running_tasks} />
        <Card label="成功任务" value={data.success_tasks} tone="good" />
        <Card label="失败任务" value={data.failed_tasks} tone="bad" />
        <Card
          label="运行端在线率"
          value={`${(data.agent_online_rate * 100).toFixed(1)}%`}
        />
      </div>
      <div className="grid-2">
        <section className="panel">
          <h2>最近活动</h2>
          {data.recent_activities.length ? (
            <Table>
              <thead>
                <tr>
                  <th>时间</th>
                  <th>任务类型</th>
                  <th>状态</th>
                  <th>摘要</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_activities.map((item) => (
                  <tr key={item.activity_id}>
                    <td>{fmt(item.timestamp)}</td>
                    <td>{taskTypeLabel(item.activity_type)}</td>
                    <td>
                      <State value={item.status} />
                    </td>
                    <td>{item.summary}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <Empty />
          )}
        </section>
        <section className="panel">
          <h2>最近任务</h2>
          {data.recent_tasks.length ? (
            <Table>
              <thead>
                <tr>
                  <th>任务</th>
                  <th>任务类型</th>
                  <th>状态</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_tasks.map((item) => (
                  <tr key={item.task_id}>
                    <td className="mono">{item.task_id.slice(0, 10)}</td>
                    <td>{taskTypeLabel(item.task_type)}</td>
                    <td>
                      <State value={item.status} />
                    </td>
                    <td>{fmt(item.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          ) : (
            <Empty />
          )}
        </section>
      </div>
    </>
  );
}

function WorkspacesPage({ user }: { user: User }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [name, setName] = useState("");
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Workspace | null>(null);
  const result = usePage<Workspace>(
    `/workspaces?paged=true&page=${page}&page_size=20&q=${encodeURIComponent(q)}`,
    [page, q],
  );
  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await apiClient("/workspaces", jsonBody({ name }));
      setName("");
      setMessage("工作区已创建");
      result.reload();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const open = async (item: Workspace) =>
    setSelected(await apiClient<Workspace>(`/workspaces/${item.workspace_id}`));
  const toggle = async () => {
    if (!selected) return;
    const next = selected.status === "ACTIVE" ? "DISABLED" : "ACTIVE";
    setSelected(
      await apiClient<Workspace>(`/workspaces/${selected.workspace_id}`, {
        method: "PATCH",
        body: JSON.stringify({ status: next }),
      }),
    );
    result.reload();
  };
  return (
    <>
      <PageTitle title="工作区" description="管理工作区及资源规模" />
      <div className="toolbar">
        <input
          placeholder="搜索工作区"
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
        />
        {user.role === "ADMIN" && (
          <form onSubmit={create} className="inline-form">
            <input
              placeholder="新工作区名称"
              value={name}
              onChange={(event) => setName(event.target.value)}
              required
            />
            <button className="primary">创建</button>
          </form>
        )}
      </div>
      {message && <div className="alert">{message}</div>}
      {result.error && <div className="alert error">{result.error}</div>}
      <Table>
        <thead>
          <tr>
            <th>名称</th>
            <th>状态</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((item) => (
            <tr
              key={item.workspace_id}
              className="clickable"
              onClick={() => open(item)}
            >
              <td>{item.name}</td>
              <td>
                <State value={item.status} />
              </td>
              <td>{fmt(item.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!result.data.items.length && !result.loading && <Empty />}
      {selected && (
        <section className="panel detail">
          <h2>{selected.name}</h2>
          <div className="metrics compact">
            <Card label="用户" value={selected.user_count ?? 0} />
            <Card label="运行端" value={selected.agent_count ?? 0} />
            <Card label="浏览器环境" value={selected.profile_count ?? 0} />
            <Card label="账号" value={selected.account_count ?? 0} />
            <Card label="任务" value={selected.task_count ?? 0} />
          </div>
          {user.role === "ADMIN" && (
            <button onClick={toggle}>
              {selected.status === "ACTIVE" ? "禁用工作区" : "启用工作区"}
            </button>
          )}
        </section>
      )}
      <PageNav page={page} pages={result.data.pages} onPage={setPage} />
    </>
  );
}

function AgentsPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const result = usePage<Agent>(
    `/agents?paged=true&page=${page}&page_size=20&q=${encodeURIComponent(q)}`,
    [page, q],
  );
  const [selected, setSelected] = useState<
    | (Agent & {
        profiles?: Profile[];
        accounts?: Account[];
        recent_tasks?: Task[];
        recent_activities?: Activity[];
      })
    | null
  >(null);
  useEffect(() => {
    const timer = window.setInterval(result.reload, 8000);
    return () => window.clearInterval(timer);
  }, [page, q]);
  const open = async (item: Agent) =>
    setSelected(await apiClient(`/agents/${item.agent_id}`));
  return (
    <>
      <PageTitle title="运行端" description="查看Windows运行端心跳和运行状态" />
      <div className="toolbar">
        <input
          placeholder="搜索运行端名称"
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
        />
        <button onClick={result.reload}>刷新</button>
        <span className="muted">自动刷新：8秒</span>
      </div>
      <Table>
        <thead>
          <tr>
            <th>运行端名称</th>
            <th>运行端ID</th>
            <th>工作区</th>
            <th>状态</th>
            <th>最近心跳</th>
            <th>浏览器环境</th>
            <th>运行中任务</th>
            <th>版本</th>
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((item) => (
            <tr
              key={item.agent_id}
              onClick={() => open(item)}
              className="clickable"
            >
              <td>{item.agent_name}</td>
              <td className="mono">{item.agent_id.slice(0, 12)}…</td>
              <td className="mono">{item.workspace_id.slice(0, 8)}…</td>
              <td>
                <State value={item.status} />
              </td>
              <td>{fmt(item.last_heartbeat)}</td>
              <td>{item.profile_count}</td>
              <td>{item.running_task_count}</td>
              <td>{item.client_version}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!result.data.items.length && !result.loading && <Empty />}
      {selected && (
        <div className="panel detail">
          <h2>{selected.agent_name}</h2>
          <p>
            运行端ID：<span className="mono">{selected.agent_id}</span>
          </p>
          <p>
            状态：
            <State value={selected.status} />
            　最近心跳：{fmt(selected.last_heartbeat)}
          </p>
          <p>
            浏览器环境：{selected.profiles?.length || 0}　账号：
            {selected.accounts?.length || 0}　最近任务：
            {selected.recent_tasks?.length || 0}
          </p>
          <button onClick={() => setSelected(null)}>关闭</button>
        </div>
      )}
      <PageNav page={page} pages={result.data.pages} onPage={setPage} />
    </>
  );
}

function AccountsPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const result = usePage<Account>(
    `/accounts?paged=true&page=${page}&page_size=20&q=${encodeURIComponent(q)}`,
    [page, q],
  );
  return (
    <>
      <PageTitle
        title="账号"
        description="来自账号注册表和服务器账号的只读映射"
      />
      <div className="toolbar">
        <input
          placeholder="搜索用户名、账号ID或浏览器环境"
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
        />
      </div>
      <Table>
        <thead>
          <tr>
            <th>X用户名</th>
            <th>X账号ID</th>
            <th>浏览器环境</th>
            <th>运行端</th>
            <th>工作区</th>
            <th>登录状态</th>
            <th>账号状态</th>
            <th>浏览器状态</th>
            <th>最近检查</th>
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((item) => (
            <tr key={item.id}>
              <td>{item.x_username || "-"}</td>
              <td className="mono">{item.x_account_id || "-"}</td>
              <td className="mono">{item.profile_id}</td>
              <td className="mono">{item.agent_id.slice(0, 8)}…</td>
              <td className="mono">{item.workspace_id.slice(0, 8)}…</td>
              <td>
                <State value={item.login_status} />
              </td>
              <td>
                <State value={item.account_status} />
              </td>
              <td>
                <State value={item.browser_status} />
              </td>
              <td>{fmt(item.last_checked)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!result.data.items.length && <Empty />}
      <PageNav page={page} pages={result.data.pages} onPage={setPage} />
    </>
  );
}

function ProfilesPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const result = usePage<Profile>(
    `/profiles?paged=true&page=${page}&page_size=20&q=${encodeURIComponent(q)}&status=${status}`,
    [page, q, status],
  );
  return (
    <>
      <PageTitle title="浏览器环境" description="浏览器环境与X账号映射" />
      <div className="toolbar">
        <input
          placeholder="搜索浏览器环境或用户名"
          value={q}
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
          <option value="RUNNING">运行中</option>
          <option value="STOPPED">已停止</option>
          <option value="LOGGED_IN">已登录</option>
          <option value="NOT_LOGGED_IN">未登录</option>
          <option value="UNKNOWN">未知</option>
        </select>
      </div>
      <Table>
        <thead>
          <tr>
            <th>环境ID</th>
            <th>运行端</th>
            <th>工作区</th>
            <th>浏览器状态</th>
            <th>X用户名</th>
            <th>X账号ID</th>
            <th>登录状态</th>
            <th>账号状态</th>
            <th>最近检查</th>
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((item) => (
            <tr key={`${item.agent_id}-${item.profile_id}`}>
              <td className="mono">{item.profile_id}</td>
              <td className="mono">{item.agent_id.slice(0, 8)}…</td>
              <td className="mono">{item.workspace_id.slice(0, 8)}…</td>
              <td>
                <State value={item.browser_status} />
              </td>
              <td>{item.x_username || "-"}</td>
              <td className="mono">{item.x_account_id || "-"}</td>
              <td>
                <State value={item.login_status} />
              </td>
              <td>
                <State value={item.account_status} />
              </td>
              <td>{fmt(item.last_checked)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!result.data.items.length && <Empty />}
      <PageNav page={page} pages={result.data.pages} onPage={setPage} />
    </>
  );
}

const READ_ONLY_TASKS = [
  "browser.open_url",
  "x.check_login",
  "x.read_profile",
  "x.read_timeline",
  "x.search",
];
function TasksPage() {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [taskType, setTaskType] = useState("x.check_login");
  const [profileId, setProfileId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Task | null>(null);
  const result = usePage<Task>(
    `/tasks?paged=true&page=${page}&page_size=20&q=${encodeURIComponent(q)}&status=${status}`,
    [page, q, status],
  );
  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const params =
        taskType === "x.search"
          ? { query: keyword }
          : taskType === "browser.open_url"
            ? { url: keyword }
            : {};
      await apiClient(
        "/tasks",
        jsonBody({
          profile_id: profileId,
          task_type: taskType,
          params,
          timeout: 30,
        }),
      );
      setMessage("只读任务已创建");
      result.reload();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const open = async (item: Task) =>
    setSelected(await apiClient<Task>(`/tasks/${item.task_id}`));
  return (
    <>
      <PageTitle
        title="任务"
        description="服务器统一任务队列，仅允许只读白名单"
      />
      <form className="panel task-form" onSubmit={create}>
        <h2>创建只读任务</h2>
        <div className="form-grid">
          <label>
            浏览器环境ID
            <input
              value={profileId}
              onChange={(event) => setProfileId(event.target.value)}
              placeholder="从浏览器环境页面复制"
              required
            />
          </label>
          <label>
            任务类型
            <select
              value={taskType}
              onChange={(event) => setTaskType(event.target.value)}
            >
              {READ_ONLY_TASKS.map((item) => (
                <option key={item} value={item}>{taskTypeLabel(item)}</option>
              ))}
            </select>
          </label>
          <label>
            {taskType === "x.search"
              ? "关键词"
              : taskType === "browser.open_url"
                ? "URL（只读打开）"
                : "参数"}
            <input
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              required={
                taskType === "x.search" || taskType === "browser.open_url"
              }
              placeholder={
                taskType === "x.search" ? "例如：Python" : "https://example.com"
              }
            />
          </label>
        </div>
        <button className="primary">创建任务</button>
        {message && <span className="form-message">{message}</span>}
      </form>
      <div className="toolbar">
        <input
          placeholder="搜索任务ID或类型"
          value={q}
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
          {[
            "PENDING",
            "RUNNING",
            "SUCCESS",
            "FAILED",
            "TIMEOUT",
            "CANCELLED",
          ].map((item) => (
            <option key={item} value={item}>{statusLabel(item)}</option>
          ))}
        </select>
      </div>
      <Table>
        <thead>
          <tr>
            <th>任务ID</th>
            <th>工作区</th>
            <th>运行端</th>
            <th>浏览器环境</th>
            <th>任务类型</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>耗时</th>
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((item) => (
            <tr
              key={item.task_id}
              className="clickable"
              onClick={() => open(item)}
            >
              <td className="mono">{item.task_id.slice(0, 12)}…</td>
              <td className="mono">{item.workspace_id.slice(0, 8)}…</td>
              <td className="mono">{item.agent_id.slice(0, 8)}…</td>
              <td className="mono">{item.profile_id}</td>
              <td>{taskTypeLabel(item.task_type)}</td>
              <td>
                <State value={item.status} />
              </td>
              <td>{fmt(item.created_at)}</td>
              <td>{item.duration?.toFixed(3)}s</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!result.data.items.length && <Empty />}
      {selected && (
        <section className="panel detail">
          <h2>任务详情</h2>
          <p className="mono">{selected.task_id}</p>
          <p>
            类型：{taskTypeLabel(selected.task_type)}　状态：
            <State value={selected.status} />
            　浏览器环境：{selected.profile_id}
          </p>
          <h2>参数</h2>
          <pre className="json-view">
            {JSON.stringify(selected.params || {}, null, 2)}
          </pre>
          <h2>结果 / 错误</h2>
          <pre className="json-view">
            {JSON.stringify(selected.result || selected.error || null, null, 2)}
          </pre>
          <button onClick={() => setSelected(null)}>关闭</button>
        </section>
      )}
      <PageNav page={page} pages={result.data.pages} onPage={setPage} />
    </>
  );
}

function ActivityPage({ user }: { user: User }) {
  const [period, setPeriod] = useState("7d");
  const [mode, setMode] = useState<"activity" | "audit">("activity");
  const activity = usePage<Activity>(
    `/activities?paged=true&page=1&page_size=100&period=${period}`,
    [period],
  );
  const auditPath =
    user.role === "MEMBER"
      ? "/activities?paged=true&page=1&page_size=1"
      : "/audit?paged=true&page=1&page_size=100";
  const audit = usePage<Audit>(auditPath, [mode, user.role]);
  const actions = (
    <div className="toolbar">
      <select
        value={period}
        onChange={(event) => setPeriod(event.target.value)}
        disabled={mode === "audit"}
      >
        <option value="today">今日</option>
        <option value="7d">近7天</option>
        <option value="30d">近30天</option>
        <option value="all">全部</option>
      </select>
      {user.role !== "MEMBER" && (
        <>
          <button
            className={mode === "activity" ? "primary" : ""}
            onClick={() => setMode("activity")}
          >
            运行记录
          </button>
          <button
            className={mode === "audit" ? "primary" : ""}
            onClick={() => setMode("audit")}
          >
            安全审计
          </button>
        </>
      )}
    </div>
  );
  return (
    <>
      <PageTitle
        title="活动记录"
        description="运行记录与按角色隔离的安全审计"
        action={actions}
      />
      {mode === "activity" ? (
        <>
          <Table>
            <thead>
              <tr>
                <th>时间</th>
                <th>工作区</th>
                <th>运行端</th>
                <th>浏览器环境</th>
                <th>任务</th>
                <th>操作</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {activity.data.items.map((item) => (
                <tr key={item.activity_id}>
                  <td>{fmt(item.timestamp)}</td>
                  <td className="mono">{item.workspace_id.slice(0, 8)}…</td>
                  <td className="mono">{item.agent_id.slice(0, 8)}…</td>
                  <td>{item.profile_id}</td>
                  <td className="mono">{item.task_id.slice(0, 10)}…</td>
                  <td>{actionLabel(item.action)}</td>
                  <td>
                    <State value={item.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
          {!activity.data.items.length && <Empty />}
        </>
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <th>时间</th>
                <th>用户</th>
                <th>工作区</th>
                <th>运行端</th>
                <th>操作</th>
                <th>资源</th>
                <th>结果</th>
                <th>IP地址</th>
              </tr>
            </thead>
            <tbody>
              {audit.data.items.map((item) => (
                <tr key={item.audit_id}>
                  <td>{fmt(item.timestamp)}</td>
                  <td className="mono">{item.user_id?.slice(0, 8) || "-"}</td>
                  <td className="mono">
                    {item.workspace_id?.slice(0, 8) || "-"}
                  </td>
                  <td className="mono">{item.agent_id?.slice(0, 8) || "-"}</td>
                  <td>{actionLabel(item.action)}</td>
                  <td>
                    {item.resource_type}:{item.resource_id}
                  </td>
                  <td>
                    <State value={item.result} />
                  </td>
                  <td>{item.ip}</td>
                </tr>
              ))}
            </tbody>
          </Table>
          {!audit.data.items.length && <Empty />}
        </>
      )}
    </>
  );
}

function StatisticsPage() {
  const [period, setPeriod] = useState("today");
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    apiClient<Record<string, any>>(`/statistics?period=${period}`)
      .then(setData)
      .catch((exc) => setError(errorText(exc)));
  }, [period]);
  if (error) return <div className="alert error">{error}</div>;
  if (!data) return <div className="loading">正在加载统计…</div>;
  return (
    <>
      <PageTitle
        title="数据统计"
        description="统计基于本地任务和活动记录"
        action={
          <select
            value={period}
            onChange={(event) => setPeriod(event.target.value)}
          >
            <option value="today">今日</option>
            <option value="7d">近7天</option>
            <option value="30d">近30天</option>
            <option value="all">全部</option>
          </select>
        }
      />
      <div className="metrics">
        <Card label="任务总数" value={data.total_tasks} />
        <Card label="成功" value={data.success_tasks} tone="good" />
        <Card label="失败" value={data.failed_tasks} tone="bad" />
        <Card label="超时" value={data.timeout_tasks} />
        <Card label="运行中" value={data.running_tasks} />
        <Card label="账号总数" value={data.accounts?.total ?? 0} />
        <Card
          label="登录账号"
          value={data.accounts?.logged_in ?? 0}
          tone="good"
        />
        <Card
          label="在线运行端"
          value={`${data.agents?.online ?? 0}/${data.agents?.total ?? 0}`}
        />
      </div>
      <section className="panel">
        <h2>任务类型统计</h2>
        <pre className="json-view">
          {JSON.stringify(data.by_task_type || {}, null, 2)}
        </pre>
      </section>
    </>
  );
}

function UsersPage({ current }: { current: User }) {
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [form, setForm] = useState({
    username: "",
    password: "",
    role: "MEMBER",
    workspace_id: "",
  });
  const [message, setMessage] = useState("");
  const [inviteForm, setInviteForm] = useState({ role: "MEMBER", workspace_id: current.workspace_id || "", expires_hours: 72 });
  const [inviteLink, setInviteLink] = useState("");
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [policyUser, setPolicyUser] = useState<User | null>(null);
  const [policy, setPolicy] = useState<{ features: Record<string, boolean>; models: Record<string, { provider_id?: string; model?: string }> } | null>(null);
  const [policyProviders, setPolicyProviders] = useState<AIProvider[]>([]);
  const [editUser, setEditUser] = useState<User | null>(null);
  const [editForm, setEditForm] = useState({ username: "", password: "", role: "MEMBER", workspace_id: "" });
  const result = usePage<User>(
    `/users?paged=true&page=${page}&page_size=20&q=${encodeURIComponent(q)}${includeDeleted ? "&include_deleted=true" : ""}`,
    [page, q, includeDeleted],
  );
  const loadInvitations = () => apiClient<Invitation[]>("/invitations").then(setInvitations).catch((exc) => setMessage(errorText(exc)));
  useEffect(() => {
    loadInvitations();
    apiClient<Workspace[]>("/workspaces").then(setWorkspaces).catch((exc) => setMessage(errorText(exc)));
  }, [current.role]);

  const createInvitation = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      const created = await apiClient<Invitation>("/invitations", jsonBody(inviteForm));
      const link = `${window.location.origin}${created.invite_path}`;
      setInviteLink(link);
      setMessage("邀请已创建。链接只在本次创建后显示，请立即复制。");
      loadInvitations();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };

  const revokeInvitation = async (item: Invitation) => {
    try {
      await apiClient(`/invitations/${item.invitation_id}`, { method: "DELETE" });
      setMessage("邀请已撤销");
      loadInvitations();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    try {
      await apiClient("/users", jsonBody(form));
      setMessage("用户已创建");
      setForm({ username: "", password: "", role: "MEMBER", workspace_id: "" });
      result.reload();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const patchUser = async (item: User, body: Record<string, unknown>) => {
    try {
      if (item.status !== "DELETED" && body.status === "DELETED" && !window.confirm(`确定要软删除用户“${item.username}”吗？用户将立即无法登录，但历史数据会保留。`)) return;
      await apiClient(`/users/${item.user_id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      result.reload();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const openEdit = (item: User) => {
    setMessage("");
    setEditUser(item);
    setEditForm({ username: item.username, password: "", role: item.role, workspace_id: item.workspace_id || "" });
  };
  const saveEdit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!editUser) return;
    const body: Record<string, unknown> = { username: editForm.username.trim() };
    if (editUser.user_id !== current.user_id) {
      body.role = editForm.role;
      body.workspace_id = current.role === "ADMIN" ? editForm.workspace_id : current.workspace_id;
    }
    if (editForm.password) body.password = editForm.password;
    try {
      await apiClient(`/users/${editUser.user_id}`, { method: "PATCH", body: JSON.stringify(body) });
      setEditUser(null);
      setMessage(`用户“${editForm.username.trim()}”已更新`);
      result.reload();
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const openPolicy = async (item: User) => {
    setMessage("");
    try {
      const [nextPolicy, providers] = await Promise.all([
        apiClient<typeof policy>(`/users/${item.user_id}/ai-policy`),
        apiClient<AIProvider[]>(`/ai/providers?status=ENABLED&workspace_id=${encodeURIComponent(item.workspace_id || "")}`),
      ]);
      setPolicyUser(item);
      setPolicy(nextPolicy);
      setPolicyProviders(providers);
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  const savePolicy = async (feature: string, enabled: boolean, provider_id = "", model = "") => {
    if (!policyUser) return;
    try {
      await apiClient(`/users/${policyUser.user_id}/ai-policy`, { ...jsonBody({ feature, enabled, provider_id: provider_id || null, model: model || null }), method: "PUT" });
      const next = await apiClient<typeof policy>(`/users/${policyUser.user_id}/ai-policy`);
      setPolicy(next);
      setMessage("AI 权限已更新");
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  return (
    <>
      <PageTitle title="用户与邀请" description="通过一次性邀请让用户安全加入工作区" />
      <form className="panel task-form" onSubmit={createInvitation}>
        <h2>创建邀请链接</h2>
        <div className="form-grid invite-form-grid">
          <label>
            用户角色
            <select value={inviteForm.role} onChange={(event) => setInviteForm({ ...inviteForm, role: event.target.value })}>
              <option value="MEMBER">成员</option>
              {current.role === "ADMIN" && <option value="OWNER">工作区负责人</option>}
            </select>
          </label>
          <label>
            工作区
            {current.role === "ADMIN" ? (
              <select value={inviteForm.workspace_id} onChange={(event) => setInviteForm({ ...inviteForm, workspace_id: event.target.value })} required>
                <option value="">请选择工作区</option>
                {workspaces.map((workspace) => <option key={workspace.workspace_id || workspace.id} value={workspace.workspace_id || workspace.id}>{workspace.name}</option>)}
              </select>
            ) : <input value={current.workspace_id || ""} disabled />}
          </label>
          <label>
            有效期
            <select value={inviteForm.expires_hours} onChange={(event) => setInviteForm({ ...inviteForm, expires_hours: Number(event.target.value) })}>
              <option value={24}>24小时</option>
              <option value={72}>3天</option>
              <option value={168}>7天</option>
            </select>
          </label>
        </div>
        <button className="primary">生成邀请链接</button>
        {inviteLink && (
          <div className="invite-link-row">
            <input value={inviteLink} readOnly />
            <button type="button" onClick={() => navigator.clipboard.writeText(inviteLink)}>复制链接</button>
          </div>
        )}
        {message && <span className="form-message block-message">{message}</span>}
      </form>
      <section className="panel">
        <h2>邀请记录</h2>
        <Table>
          <thead><tr><th>工作区</th><th>角色</th><th>状态</th><th>到期时间</th><th>操作</th></tr></thead>
          <tbody>
            {invitations.map((item) => (
              <tr key={item.invitation_id}>
                <td>{item.workspace_name || item.workspace_id}</td>
                <td>{item.role === "OWNER" ? "负责人" : "成员"}</td>
                <td><State value={item.status} /></td>
                <td>{fmt(item.expires_at)}</td>
                <td><button disabled={item.status !== "ACTIVE"} onClick={() => revokeInvitation(item)}>撤销</button></td>
              </tr>
            ))}
          </tbody>
        </Table>
        {!invitations.length && <Empty />}
      </section>
      <form className="panel task-form" onSubmit={create}>
        <h2>管理员直接创建用户</h2>
        <div className="form-grid">
          <label>
            用户名
            <input
              value={form.username}
              onChange={(event) =>
                setForm({ ...form, username: event.target.value })
              }
              required
            />
          </label>
          <label>
            密码
            <input
              type="password"
              minLength={8}
              value={form.password}
              onChange={(event) =>
                setForm({ ...form, password: event.target.value })
              }
              required
            />
          </label>
          <label>
            角色
            <select
              value={form.role}
              onChange={(event) =>
                setForm({ ...form, role: event.target.value })
              }
            >
              <option value="MEMBER">成员</option>
              <option value="OWNER">工作区负责人</option>
              {current.role === "ADMIN" && <option value="ADMIN">系统管理员</option>}
            </select>
          </label>
          <label>
            工作区
            {current.role === "ADMIN" ? (
              <select value={form.workspace_id} onChange={(event) => setForm({ ...form, workspace_id: event.target.value })} required>
                <option value="">请选择工作区</option>
                {workspaces.map((workspace) => <option key={workspace.workspace_id} value={workspace.workspace_id}>{workspace.name}</option>)}
              </select>
            ) : <input value={workspaces.find((workspace) => workspace.workspace_id === current.workspace_id)?.name || current.workspace_id || ""} disabled />}
          </label>
        </div>
        <button className="primary">创建用户</button>
        {message && <span className="form-message">{message}</span>}
      </form>
      <div className="toolbar">
        <input
          placeholder="搜索用户名"
          value={q}
          onChange={(event) => {
            setQ(event.target.value);
            setPage(1);
          }}
        />
        {current.role === "ADMIN" && (
          <label className="checkbox-label">
            <input type="checkbox" checked={includeDeleted} onChange={(event) => { setIncludeDeleted(event.target.checked); setPage(1); }} />
            显示已删除用户
          </label>
        )}
      </div>
      <Table>
        <thead>
          <tr>
            <th>用户名</th>
            <th>角色</th>
            <th>工作区</th>
            <th>状态</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {result.data.items.map((item) => (
            <tr key={item.user_id}>
              <td>{item.username}</td>
              <td>
                <select
                  value={item.role}
                  disabled={item.user_id === current.user_id}
                  onChange={(event) =>
                    patchUser(item, { role: event.target.value })
                  }
                >
                  <option value="MEMBER">成员</option>
                  <option value="OWNER">工作区负责人</option>
                  {current.role === "ADMIN" && <option value="ADMIN">系统管理员</option>}
                </select>
              </td>
              <td>
                <div>{item.workspace_name || "平台全局"}</div>
                <small className="mono">{item.workspace_id || "全局"}</small>
              </td>
              <td>
                <State value={item.status} />
              </td>
              <td>{fmt(item.created_at)}</td>
              <td className="user-actions">
                <button onClick={() => openEdit(item)}>编辑用户</button>
                <button onClick={() => void openPolicy(item)}>AI 权限</button>
                <button
                  disabled={item.user_id === current.user_id || (item.status === "DELETED" && current.role !== "ADMIN")}
                  onClick={() =>
                    patchUser(item, {
                      status: item.status === "DELETED" ? "ACTIVE" : current.role === "ADMIN" ? "DELETED" : item.status === "ACTIVE" ? "DISABLED" : "ACTIVE",
                    })
                  }
                >
                  {item.status === "DELETED" ? "恢复" : current.role === "ADMIN" ? "删除" : item.status === "ACTIVE" ? "停用" : "启用"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
      {!result.data.items.length && <Empty />}
      <PageNav page={page} pages={result.data.pages} onPage={setPage} />
      {editUser && (
        <div className="modal-backdrop" onClick={() => setEditUser(null)}>
          <form className="modal-panel user-edit-modal" onSubmit={saveEdit} onClick={(event) => event.stopPropagation()}>
            <div className="modal-header">
              <div><h2>编辑用户</h2><span className="muted">修改账号资料、所属工作区或重置登录密码</span></div>
              <button type="button" onClick={() => setEditUser(null)}>关闭</button>
            </div>
            <div className="edit-user-grid">
              <label>
                用户名
                <small>用户登录系统时使用的账号名称</small>
                <input value={editForm.username} onChange={(event) => setEditForm({ ...editForm, username: event.target.value })} minLength={3} required />
              </label>
              <label>
                重置密码（可不填）
                <small>留空表示保持原密码；填写后至少需要 8 位</small>
                <input type="password" value={editForm.password} onChange={(event) => setEditForm({ ...editForm, password: event.target.value })} minLength={8} autoComplete="new-password" />
              </label>
              <label>
                用户角色
                <small>成员使用功能；负责人管理工作区；管理员管理整个平台</small>
                <select value={editForm.role} disabled={editUser.user_id === current.user_id} onChange={(event) => setEditForm({ ...editForm, role: event.target.value })}>
                  <option value="MEMBER">成员</option>
                  <option value="OWNER">工作区负责人</option>
                  {current.role === "ADMIN" && <option value="ADMIN">系统管理员</option>}
                </select>
              </label>
              <label>
                所属工作区
                <small>决定该用户能看到哪一个工作区的浏览器和业务数据</small>
                {current.role === "ADMIN" ? (
                  <select value={editForm.workspace_id} disabled={editUser.user_id === current.user_id} onChange={(event) => setEditForm({ ...editForm, workspace_id: event.target.value })} required>
                    <option value="">请选择工作区</option>
                    {workspaces.map((workspace) => <option key={workspace.workspace_id || workspace.id} value={workspace.workspace_id || workspace.id}>{workspace.name}</option>)}
                  </select>
                ) : <input value={workspaces.find((workspace) => workspace.workspace_id === current.workspace_id)?.name || current.workspace_id || ""} disabled />}
              </label>
            </div>
            {editUser.user_id === current.user_id && <p className="form-help">当前登录账号只能在这里修改用户名和密码，不能修改自己的角色或工作区，避免失去管理权限。</p>}
            <div className="modal-actions"><button type="button" onClick={() => setEditUser(null)}>取消</button><button className="primary">保存修改</button></div>
          </form>
        </div>
      )}
      {policyUser && policy && (
        <div className="modal-backdrop" onClick={() => setPolicyUser(null)}>
          <section className="modal-panel ai-policy-modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-header"><div><h2>AI 功能权限与模型</h2><span className="muted">为 {policyUser.username} 分配允许使用的 AI 功能、服务商和模型</span></div><button onClick={() => setPolicyUser(null)}>关闭</button></div>
            <div className="policy-header"><span>功能权限</span><span>使用的 AI 服务商</span><span>使用的模型</span></div>
            {(["CHAT", "WRITING", "ANALYSIS", "TASKS", "IMAGES"] as const).map((feature) => {
              const featureMeta = {
                CHAT: ["AI 聊天", "与 AI 进行日常问答和连续对话"],
                WRITING: ["AI 话术", "生成文案、回复内容和沟通话术"],
                ANALYSIS: ["AI 分析", "分析账号、内容和业务数据"],
                TASKS: ["AI 任务", "让 AI 生成并规划自动化任务"],
                IMAGES: ["AI 生图", "使用文字描述生成图片"],
              }[feature];
              const assignment = policy.models[feature] || {};
              const provider = policyProviders.find((item) => item.provider_id === assignment.provider_id);
              const models = provider ? Array.from(new Set([...(provider.models || []), provider.default_model].filter(Boolean))) : [];
              return <div className="policy-row" key={feature}>
                <label className="check-row policy-feature"><input type="checkbox" checked={Boolean(policy.features[feature])} onChange={(event) => void savePolicy(feature, event.target.checked, assignment.provider_id || "", assignment.model || "")} /><span><strong>{featureMeta[0]}</strong><small>{featureMeta[1]}</small></span></label>
                <select value={assignment.provider_id || ""} disabled={!policy.features[feature]} onChange={(event) => void savePolicy(feature, true, event.target.value, "")}><option value="">自动使用工作区默认</option>{policyProviders.map((item) => <option key={item.provider_id} value={item.provider_id}>{item.name}</option>)}</select>
                <select value={assignment.model || ""} disabled={!policy.features[feature] || !provider} onChange={(event) => void savePolicy(feature, true, assignment.provider_id || "", event.target.value)}><option value="">默认模型</option>{models.map((item) => <option key={item} value={item}>{item}</option>)}</select>
              </div>;
            })}
          </section>
        </div>
      )}
    </>
  );
}

function SettingsPage({ user }: { user: User }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [message, setMessage] = useState("");
  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    if (newPassword !== confirmPassword) {
      setMessage("两次输入的新密码不一致");
      return;
    }
    try {
      const response = await apiClient<{ access_token: string }>("/auth/password", jsonBody({ current_password: currentPassword, new_password: newPassword }));
      authStore.set(response.access_token);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setMessage("密码已更新，下次登录请使用新密码");
    } catch (exc) {
      setMessage(errorText(exc));
    }
  };
  return (
    <>
      <PageTitle
        title="账号与安全"
        description="管理你的个人账号与登录密码"
      />
      <section className="panel account-summary">
        <h2>个人资料</h2>
        <div className="account-summary-grid">
          <div><span>用户名</span><strong>{user.username}</strong></div>
          <div><span>角色</span><strong>{user.role}</strong></div>
          <div><span>工作区</span><strong className="mono">{user.workspace_id || "平台全局"}</strong></div>
        </div>
      </section>
      <form className="panel password-form" onSubmit={changePassword}>
        <h2>修改密码</h2>
        <label>当前密码<input type="password" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} autoComplete="current-password" required /></label>
        <label>新密码<input type="password" minLength={8} value={newPassword} onChange={(event) => setNewPassword(event.target.value)} autoComplete="new-password" required /></label>
        <label>确认新密码<input type="password" minLength={8} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} autoComplete="new-password" required /></label>
        <button className="primary">更新密码</button>
        {message && <span className="form-message">{message}</span>}
      </form>
      <section className="panel">
        <h2>安全说明</h2>
        <ul className="plain-list">
          <li>JWT仅保存在当前浏览器会话的sessionStorage中。</li>
          <li>所有请求通过统一apiClient发送，401会自动返回登录页。</li>
          <li>
            工作区、运行端、浏览器环境、账号和任务权限由服务器最终判断。
          </li>
          <li>Web服务器不会连接Windows的127.0.0.1:19876。</li>
          <li>本阶段只允许只读任务类型。</li>
        </ul>
      </section>
    </>
  );
}

export function ResourcesPage({
  resource,
  user,
}: {
  resource: string;
  user: User;
}) {
  switch (resource) {
    case "workspaces":
      return <WorkspacesPage user={user} />;
    case "agents":
      return <AgentsPage />;
    case "accounts":
      return <AccountsPage />;
    case "profiles":
      return <ProfilesPage />;
    case "tasks":
      return <TasksPage />;
    case "activity":
      return <ActivityPage user={user} />;
    case "statistics":
      return <StatisticsPage />;
    case "users":
      return <UsersPage current={user} />;
    case "settings":
      return <SettingsPage user={user} />;
    default:
      return <DashboardPage />;
  }
}
