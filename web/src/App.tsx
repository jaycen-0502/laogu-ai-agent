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
const AITranslationPage = lazy(() => import("./pages/ai_translation").then((module) => ({ default: module.AITranslationPage })));
const AIImagesPage = lazy(() => import("./pages/ai_images").then((module) => ({ default: module.AIImagesPage })));
const AIAnalysisPage = lazy(() => import("./pages/ai_analysis").then((module) => ({ default: module.AIAnalysisPage })));
const AIWritingPage = lazy(() => import("./pages/ai_writing").then((module) => ({ default: module.AIWritingPage })));
const AITasksPage = lazy(() => import("./pages/ai_tasks").then((module) => ({ default: module.AITasksPage })));
const ControlCenterPage = lazy(() => import("./pages/control_center").then((module) => ({ default: module.ControlCenterPage })));
const OpsMetricsPage = lazy(() => import("./pages/ops_metrics").then((module) => ({ default: module.OpsMetricsPage })));
const LicensesPage = lazy(() => import("./pages/licenses").then((module) => ({ default: module.LicensesPage })));
const TelegramTranslationPage = lazy(() => import("./pages/telegram_translation").then((module) => ({ default: module.TelegramTranslationPage })));
const ProxyNodesPage = lazy(() => import("./pages/proxy_nodes").then((module) => ({ default: module.ProxyNodesPage })));

const menu = [
  ["/ai/chat", "AI 聊天", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/translation", "AI 翻译", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/images", "AI 生图", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/analysis", "AI 分析", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/writing", "AI 话术", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ai/tasks", "AI 任务", ["ADMIN", "OWNER", "MEMBER"]],
  ["/control-center", "统一控制中心", ["ADMIN", "OWNER", "MEMBER"]],
  ["/ops", "运维监控", ["ADMIN"]],
  ["/licenses", "远程授权", ["ADMIN"]],
  ["/telegram-translation", "Telegram 翻译", ["ADMIN"]],
  ["/ai-providers", "AI 服务商", ["ADMIN", "OWNER", "MEMBER"]],
  ["/dashboard", "控制台", ["ADMIN", "OWNER", "MEMBER"]],
  ["/workspaces", "工作区", ["ADMIN", "OWNER", "MEMBER"]],
  ["/users", "用户与邀请", ["ADMIN", "OWNER"]],
  ["/agents", "运行端", ["ADMIN", "OWNER", "MEMBER"]],
  ["/accounts", "账号", ["ADMIN", "OWNER", "MEMBER"]],
  ["/profiles", "浏览器环境", ["ADMIN", "OWNER", "MEMBER"]],
  ["/proxy-nodes", "代理节点", ["ADMIN", "OWNER"]],
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

const iconPaths: Record<string, React.ReactNode> = {
  ai: <><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/><circle cx="12" cy="12" r="5"/><path d="m9.8 12 1.5 1.5 3-3"/></>,
  control: <><path d="M4 7h10M18 7h2M4 17h2M10 17h10"/><path d="M14 4v6M6 14v6"/></>,
  ops: <><path d="M4 13h3l2-6 4 10 2-6h5"/><path d="M4 20h16"/></>,
  license: <><path d="M12 3 5 6v5c0 4.4 2.9 8.4 7 10 4.1-1.6 7-5.6 7-10V6z"/><path d="m9 12 2 2 4-4"/></>,
  message: <><path d="M5 5h14v11H9l-4 4z"/><path d="M8 9h8M8 12h5"/></>,
  provider: <><rect x="5" y="5" width="14" height="14" rx="3"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M19 9h3M2 15h3M19 15h3"/></>,
  dashboard: <><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></>,
  workspace: <><path d="M4 20h16M6 20V8l6-4 6 4v12"/><path d="M9 11h2M13 11h2M9 15h2M13 15h2"/></>,
  users: <><circle cx="9" cy="8" r="3"/><path d="M3.5 20c.6-4 2.5-6 5.5-6s4.9 2 5.5 6"/><path d="M16 5.5a3 3 0 0 1 0 5.5M17 14c2.2.6 3.5 2.6 3.8 5"/></>,
  agent: <><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></>,
  account: <><circle cx="12" cy="8" r="4"/><path d="M4 21c.8-5 3.4-7 8-7s7.2 2 8 7"/></>,
  profile: <><rect x="4" y="5" width="16" height="14" rx="2"/><circle cx="9" cy="10" r="2"/><path d="M7 16c.6-2 3.4-2 4 0M14 9h3M14 13h3"/></>,
  task: <><path d="M9 6h11M9 12h11M9 18h11"/><path d="m4 6 1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2"/></>,
  activity: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  stats: <><path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/></>,
  script: <><path d="m9 7-5 5 5 5M15 7l5 5-5 5M13 4l-2 16"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z"/></>,
  proxy: <><path d="M7 7h10M7 12h10M7 17h6"/><circle cx="4" cy="7" r="1"/><circle cx="4" cy="12" r="1"/><circle cx="4" cy="17" r="1"/></>,
  menu: <><path d="M4 7h16M4 12h16M4 17h16"/></>,
  close: <><path d="m6 6 12 12M18 6 6 18"/></>,
};

const navIcons: Record<string, string> = {
  "/control-center": "control", "/ops": "ops", "/licenses": "license", "/telegram-translation": "message",
  "/ai-providers": "provider", "/dashboard": "dashboard", "/workspaces": "workspace", "/users": "users",
  "/agents": "agent", "/accounts": "account", "/profiles": "profile", "/tasks": "task", "/activity": "activity",
  "/statistics": "stats", "/scripts": "script", "/script-runs": "activity", "/settings": "settings", "/proxy-nodes": "proxy",
};

function Icon({ name, className = "" }: { name: string; className?: string }) {
  return <svg className={`ui-icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{iconPaths[name] || iconPaths.dashboard}</svg>;
}

function Layout({ user }: { user: User }) {
  const location = useLocation();
  const navigate = useNavigate();
  const aiMenuPaths = new Set(["/ai/chat", "/ai/translation", "/ai/images", "/ai/analysis", "/ai/writing", "/ai/tasks"]);
  const [aiExpanded, setAiExpanded] = useState(() => location.pathname.startsWith("/ai/"));
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const isPlatformAdmin = user.role === "ADMIN";
  const logout = () => {
    authStore.clear();
    navigate("/login");
  };
  const memberPaths = new Set(["/dashboard", "/control-center", "/profiles", "/scripts", "/script-runs", "/ai/chat", "/ai/translation", "/ai/images", "/ai/writing", "/ai/analysis", "/ai/tasks"]);
  const canSee = (path: string, roles: readonly string[]) => {
    if (!roles.some((role) => role === user.role)) return false;
    if (user.role !== "MEMBER") return true;
    if (!memberPaths.has(path)) return false;
    const feature = path === "/ai/chat" ? "CHAT" : path === "/ai/translation" ? "TRANSLATE" : path === "/ai/images" ? "IMAGES" : path === "/ai/writing" ? "WRITING" : path === "/ai/analysis" ? "ANALYSIS" : path === "/ai/tasks" ? "TASKS" : "";
    return !feature || user.permissions?.[feature] !== false;
  };
  useEffect(() => setSidebarOpen(false), [location.pathname]);
  return (
    <div className="app-shell">
      <aside id="primary-navigation" className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="brand">
          <span className="brand-mark"><img src="/laogu-control-center-logo.svg" alt="" /></span>
          <span className="brand-copy"><strong>{isPlatformAdmin ? "老谷平台管理" : "老谷用户工作台"}</strong><small>Operations Console</small></span>
        </div>
        <div className="workspace-card">
          <span className="workspace-label">当前工作区</span>
          <strong>{user.workspace_name || (isPlatformAdmin ? "平台全局" : "未分配工作区")}</strong>
          <small>ID：{user.workspace_id || "全局管理"}</small>
        </div>
        <nav>
          <button type="button" className={`nav-group-toggle ${location.pathname.startsWith("/ai/") ? "active" : ""}`} onClick={() => setAiExpanded((value) => !value)}>
            <span className="nav-label"><Icon name="ai" />AI 功能</span><span className="nav-chevron" aria-hidden="true">{aiExpanded ? "−" : "+"}</span>
          </button>
          {aiExpanded && <div className="nav-group-items">
            {menu.filter(([path, , roles]) => aiMenuPaths.has(path) && canSee(path, roles)).map(([path, label]) => (
              <Link
                key={path}
                className={location.pathname === path || location.pathname.startsWith(`${path}/`) ? "active" : ""}
                to={path}
              >
                <Icon name="ai" />{label}
              </Link>
            ))}
          </div>}
          {menu
            .filter(([path, , roles]) => !aiMenuPaths.has(path) && canSee(path, roles))
            .map(([path, label]) => (
              <Link key={path} className={location.pathname === path || location.pathname.startsWith(`${path}/`) ? "active" : ""} to={path}><Icon name={navIcons[path] || "dashboard"} />{label}</Link>
            ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="role-badge">{roleNames[user.role] || user.role}</span>
          <button className="link-button" onClick={logout}>
            退出登录
          </button>
        </div>
      </aside>
      {sidebarOpen && <button className="sidebar-scrim" type="button" aria-label="关闭导航菜单" onClick={() => setSidebarOpen(false)} />}
      <main className="main-area">
        <header className="topbar">
          <button className="mobile-menu-button" type="button" aria-label={sidebarOpen ? "关闭导航菜单" : "打开导航菜单"} aria-expanded={sidebarOpen} aria-controls="primary-navigation" onClick={() => setSidebarOpen((value) => !value)}><Icon name={sidebarOpen ? "close" : "menu"} /></button>
          <div className="topbar-title">
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
    const allowed = ["/dashboard", "/control-center", "/profiles", "/scripts", "/script-runs", "/ai/chat", "/ai/translation", "/ai/images", "/ai/writing", "/ai/analysis", "/ai/tasks"];
    const feature = location.pathname.startsWith("/ai/chat") ? "CHAT" : location.pathname.startsWith("/ai/translation") ? "TRANSLATE" : location.pathname.startsWith("/ai/images") ? "IMAGES" : location.pathname.startsWith("/ai/writing") ? "WRITING" : location.pathname.startsWith("/ai/analysis") ? "ANALYSIS" : location.pathname.startsWith("/ai/tasks") ? "TASKS" : "";
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
        <Route path="ai/translation" element={<Suspense fallback={<div className="loading">正在加载 AI 翻译…</div>}><AITranslationPage /></Suspense>} />
        <Route path="ai/images" element={<Suspense fallback={<div className="loading">正在加载 AI 生图…</div>}><AIImagesPage /></Suspense>} />
        <Route path="ai/analysis" element={<Suspense fallback={<div className="loading">正在加载 AI 分析…</div>}><AIAnalysisPage /></Suspense>} />
        <Route path="ai/writing" element={<Suspense fallback={<div className="loading">正在加载 AI 话术…</div>}><AIWritingPage /></Suspense>} />
        <Route path="ai/tasks" element={<Suspense fallback={<div className="loading">正在加载 AI 任务…</div>}><AITasksPage /></Suspense>} />
        <Route path="control-center" element={<Suspense fallback={<div className="loading">正在加载统一控制中心…</div>}><ControlCenterPage /></Suspense>} />
        <Route path="ops" element={<Suspense fallback={<div className="loading">正在加载运维监控…</div>}><OpsMetricsPage /></Suspense>} />
        <Route path="licenses" element={<Suspense fallback={<div className="loading">正在加载远程授权…</div>}><LicensesPage /></Suspense>} />
        <Route path="telegram-translation" element={<Suspense fallback={<div className="loading">正在加载 Telegram 翻译…</div>}><TelegramTranslationPage /></Suspense>} />
        <Route path="proxy-nodes" element={<Suspense fallback={<div className="loading">正在加载代理节点工具…</div>}><ProxyNodesPage /></Suspense>} />
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
          <span className="brand-mark"><img src="/laogu-control-center-logo.svg" alt="" /></span>
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
          <span className="brand-mark"><img src="/laogu-control-center-logo.svg" alt="" /></span>
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
