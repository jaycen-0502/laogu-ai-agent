import { lazy, Suspense, useEffect, useState } from "react";
import {
  Link,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { apiClient, authStore, ApiError, jsonBody } from "./api/client";
import type { User } from "./types";
import { DashboardPage, ResourcesPage } from "./pages";

const ScriptsPage = lazy(() => import("./pages/scripts").then((module) => ({ default: module.ScriptsPage })));
const ScriptEditorPage = lazy(() => import("./pages/scripts").then((module) => ({ default: module.ScriptEditorPage })));
const ScriptRunsPage = lazy(() => import("./pages/scripts").then((module) => ({ default: module.ScriptRunsPage })));
const AIProvidersPage = lazy(() => import("./pages/ai_providers").then((module) => ({ default: module.AIProvidersPage })));
const AIChatPage = lazy(() => import("./pages/ai_chat").then((module) => ({ default: module.AIChatPage })));
const AIImagesPage = lazy(() => import("./pages/ai_images").then((module) => ({ default: module.AIImagesPage })));
const AIAnalysisPage = lazy(() => import("./pages/ai_analysis").then((module) => ({ default: module.AIAnalysisPage })));
const AIWritingPage = lazy(() => import("./pages/ai_writing").then((module) => ({ default: module.AIWritingPage })));
const AITasksPage = lazy(() => import("./pages/ai_tasks").then((module) => ({ default: module.AITasksPage })));
const ControlCenterPage = lazy(() => import("./pages/control_center").then((module) => ({ default: module.ControlCenterPage })));
const OpsMetricsPage = lazy(() => import("./pages/ops_metrics").then((module) => ({ default: module.OpsMetricsPage })));
const LicensesPage = lazy(() => import("./pages/licenses").then((module) => ({ default: module.LicensesPage })));

const menu = [
  ["/ai/chat", "AI 聊天", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/images", "AI 生图", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/analysis", "AI 分析", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/writing", "AI 话术", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/tasks", "AI 任务", ["ADMIN", "OWNER", "MEMBER"]],
  ["/control-center", "统一控制中心", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ops", "运维监控", ["ADMIN"]],
  ["/licenses", "远程授权", ["ADMIN"]],
  ["/ai-providers", "AI 服务商", ["ADMIN", "OWNER", "MEMBER"]],
  ["/dashboard", "控制台", ["ADMIN", "OWNER", "MEMBER"]],
  ["/workspaces", "工作区", ["ADMIN", "OWNER", "MEMBER"]],
  ["/users", "用户与邀请", ["ADMIN", "OWNER"]],
  ["/agents", "运行端", ["ADMIN", "OWNER", "MEMBER"]],
  ["/accounts", "账号", ["ADMIN", "OWNER", "MEMBER"]],
  ["/profiles", "浏览器环境", ["ADMIN", "OWNER", "MEMBER"]],
  ["/tasks", "任务", ["ADMIN", "OWNER", "MEMBER"]],
  ["/activity", "活动记录", ["ADMIN", "OWNER", "MEMBER"]],
  ["/statistics", "数据统计", ["ADMIN", "OWNER", "MEMBER"]],
  ["/scripts", "脚本中心", ["ADMIN", "OWNER", "MEMBER"]],
  ["/script-runs", "脚本运行历史", ["ADMIN", "OWNER", "MEMBER"]],
  ["/settings", "账号与安全", ["ADMIN", "OWNER", "MEMBER"]],
] as const;

const roleNames: Record<string, string> = {
  ADMIN: "系统管理员",
  OWNER: "工作区负责人",
  MEMBER: "成员",
};

function Layout({ user }: { user: User }) {
  const location = useLocation();
  const navigate = useNavigate();
  const isPlatformAdmin = user.role === "ADMIN";
  const logout = () => {
    authStore.clear();
    navigate("/login");
  };
  const memberPaths = new Set(["/dashboard", "/control-center", "/profiles", "/ai/chat", "/ai/writing", "/ai/analysis", "/ai/tasks"]);
  const canSee = (path: string, roles: readonly string[]) => {
    if (!roles.some((role) => role === user.role)) return false;
    if (user.role !== "MEMBER") return true;
    if (!memberPaths.has(path)) return false;
    const feature = path === "/ai/chat" ? "CHAT" : path === "/ai/writing" ? "WRITING" : path === "/ai/analysis" ? "ANALYSIS" : path === "/ai/tasks" ? "TASKS" : "";
    return !feature || user.permissions?.[feature] !== false;
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">L</span>
          <span>{isPlatformAdmin ? "老谷平台管理" : "老谷用户工作台"}</span>
        </div>
        <div className="workspace-card">
          <span className="workspace-label">当前工作区</span>
          <strong>{user.workspace_name || (isPlatformAdmin ? "平台全局" : "未分配工作区")}</strong>
          <small>ID：{user.workspace_id || "全局管理"}</small>
          工作区：{user.workspace_id || "全局"}
        </div>
        <nav>
          {menu
            .filter(([path, , roles]) => canSee(path, roles))
            .map(([path, label]) => (
              <Link
                key={path}
                className={location.pathname === path || location.pathname.startsWith(`${path}/`) ? "active" : ""}
                to={path}
              >
                {label}
              </Link>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="role-badge">{roleNames[user.role] || user.role}</span>
          <button className="link-button" onClick={logout}>
            退出登录
          </button>
        </div>
      </aside>
      <main className="main-area">
        <header className="topbar">
          <div>
            <strong>
              {menu.find(([path]) => location.pathname === path || location.pathname.startsWith(`${path}/`))?.[1] ||
                "管理后台"}
            </strong>
            <span className="muted">{isPlatformAdmin ? "平台运营中心" : "工作区服务中心"}</span>
          </div>
          <Link
            className="user-chip account-link"
            to="/settings"
            title="打开账户与安全，修改登录密码"
            aria-label="账户与安全"
          >
            <span>{user.username}</span>
            <span className="account-link-label">账户与安全</span>
          </Link>
        </header>
        <section className="content">
          <Outlet />
        </section>
      </main>
    </div>
  );
}

function Protected({ user }: { user: User | null }) {
  if (!user) return <Navigate to="/login" replace />;
  const location = useLocation();
  if (user.role === "MEMBER") {
    const allowed = ["/dashboard", "/control-center", "/profiles", "/ai/chat", "/ai/writing", "/ai/analysis", "/ai/tasks"];
    const feature = location.pathname.startsWith("/ai/chat") ? "CHAT" : location.pathname.startsWith("/ai/writing") ? "WRITING" : location.pathname.startsWith("/ai/analysis") ? "ANALYSIS" : location.pathname.startsWith("/ai/tasks") ? "TASKS" : "";
    if (!allowed.some((path) => location.pathname === path || location.pathname.startsWith(`${path}/`)) || (feature && user.permissions?.[feature] === false)) {
      return <Navigate to="/dashboard" replace />;
    }
  }
  return <Layout user={user} />;
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  useEffect(() => {
    if (!authStore.get()) {
      setLoading(false);
      return;
    }
    apiClient<User>("/auth/me")
      .then(setUser)
      .catch(() => {
        authStore.clear();
        setUser(null);
      })
      .finally(() => setLoading(false));
  }, []);
  if (loading) return <div className="loading-screen">正在检查登录状态…</div>;
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <LoginPage
            onLogin={(next) => {
              setUser(next);
              navigate("/dashboard");
            }}
          />
        }
      />
      <Route
        path="/invite/:token"
        element={
          <InvitePage
            onAccepted={(next, token) => {
              authStore.set(token);
              setUser(next);
              navigate("/dashboard");
            }}
          />
        }
      />
      <Route element={<Protected user={user} />}>
        <Route path="ai/chat" element={<Suspense fallback={<div className="loading">正在加载 AI 聊天…</div>}><AIChatPage /></Suspense>} />
        <Route path="ai/images" element={<Suspense fallback={<div className="loading">正在加载 AI 生图…</div>}><AIImagesPage /></Suspense>} />
        <Route path="ai/analysis" element={<Suspense fallback={<div className="loading">正在加载 AI 分析…</div>}><AIAnalysisPage /></Suspense>} />
        <Route path="ai/writing" element={<Suspense fallback={<div className="loading">正在加载 AI 话术…</div>}><AIWritingPage /></Suspense>} />
        <Route path="ai/tasks" element={<Suspense fallback={<div className="loading">正在加载 AI 任务…</div>}><AITasksPage /></Suspense>} />
        <Route path="control-center" element={<Suspense fallback={<div className="loading">正在加载统一控制中心…</div>}><ControlCenterPage /></Suspense>} />
        <Route path="ops" element={<Suspense fallback={<div className="loading">正在加载运维监控…</div>}><OpsMetricsPage /></Suspense>} />
        <Route path="licenses" element={<Suspense fallback={<div className="loading">正在加载远程授权…</div>}><LicensesPage /></Suspense>} />
        <Route path="ai-providers" element={<Suspense fallback={<div className="loading">正在加载 AI 服务商…</div>}><AIProvidersPage user={user!} /></Suspense>} />
        <Route index element={<Navigate to="/dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="scripts" element={<Suspense fallback={<div className="loading">正在加载脚本中心…</div>}><ScriptsPage user={user!} /></Suspense>} />
        <Route path="scripts/:id" element={<Suspense fallback={<div className="loading">正在加载脚本编辑器…</div>}><ScriptEditorPage user={user!} /></Suspense>} />
        <Route path="script-runs" element={<Suspense fallback={<div className="loading">正在加载运行历史…</div>}><ScriptRunsPage /></Suspense>} />
        {[
          "workspaces",
          "agents",
          "accounts",
          "profiles",
          "tasks",
          "activity",
          "statistics",
          "users",
          "settings",
        ].map((resource) => (
          <Route
            key={resource}
            path={resource}
            element={<ResourcesPage resource={resource} user={user!} />}
          />
        ))}
      </Route>
      <Route
        path="*"
        element={<Navigate to={user ? "/dashboard" : "/login"} replace />}
      />
    </Routes>
  );
}

export function LoginPage({ onLogin }: { onLogin: (user: User) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const response = await apiClient<{ access_token: string }>(
        "/auth/login",
        jsonBody({ username, password }),
      );
      authStore.set(response.access_token);
      onLogin(await apiClient<User>("/auth/me"));
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "登录失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-page">
      <div className="login-card">
        <div className="brand login-brand">
          <span className="brand-mark">L</span>
          <span>老谷 AI 工作台</span>
        </div>
        <h1>欢迎回来</h1>
        <p className="muted">登录你的工作区或平台管理中心</p>
        <form onSubmit={submit}>
          <label>
            用户名
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label>
            密码
            <input
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          {error && <div className="alert error">{error}</div>}
          <button className="primary full" disabled={busy}>
            {busy ? "登录中…" : "登录"}
          </button>
        </form>
      </div>
    </div>
  );
}

type InvitationInfo = {
  workspace_name: string;
  role: "OWNER" | "MEMBER";
  expires_at: string;
};

function InvitePage({ onAccepted }: { onAccepted: (user: User, token: string) => void }) {
  const { token = "" } = useParams();
  const [invitation, setInvitation] = useState<InvitationInfo | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    apiClient<InvitationInfo>(`/auth/invitations/${encodeURIComponent(token)}`)
      .then(setInvitation)
      .catch((exc) => setError(exc instanceof ApiError ? exc.message : "邀请链接无效或已过期"));
  }, [token]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await apiClient<{ access_token: string; user: User }>(
        `/auth/invitations/${encodeURIComponent(token)}/accept`,
        jsonBody({ username, password }),
      );
      onAccepted(result.user, result.access_token);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : "注册失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card invite-card">
        <div className="brand login-brand">
          <span className="brand-mark">L</span>
          <span>老谷 AI 工作台</span>
        </div>
        <h1>加入工作区</h1>
        {invitation && (
          <p className="muted">
            你将加入“{invitation.workspace_name}”，身份为
            {invitation.role === "OWNER" ? "负责人" : "成员"}。
          </p>
        )}
        {invitation ? (
          <form onSubmit={submit}>
            <label>
              用户名
              <input value={username} onChange={(event) => setUsername(event.target.value)} minLength={3} maxLength={120} autoComplete="username" required />
            </label>
            <label>
              设置密码
              <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" minLength={8} autoComplete="new-password" required />
            </label>
            <label>
              确认密码
              <input value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} type="password" minLength={8} autoComplete="new-password" required />
            </label>
            {error && <div className="alert error">{error}</div>}
            <button className="primary full" disabled={busy}>{busy ? "正在创建账号…" : "接受邀请并进入"}</button>
          </form>
        ) : error ? (
          <div className="alert error">{error}</div>
        ) : (
          <div className="loading">正在验证邀请…</div>
        )}
        <Link className="invite-login-link" to="/login">已有账号？返回登录</Link>
      </div>
    </div>
  );
}
