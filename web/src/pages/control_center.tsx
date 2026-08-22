import { useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { ControlOverview, ControlProfileDetail } from "../types";

const fmt = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "-";
const numberText = (value?: number | null) => (value ?? 0).toLocaleString("zh-CN");
const errorText = (value: unknown) => value instanceof ApiError ? value.message : "请求失败，请稍后重试";
const stateLabel: Record<string, string> = {
  ONLINE: "在线", OFFLINE: "离线", PENDING: "等待中", DISPATCHED: "已派发", RUNNING: "运行中",
  SUCCESS: "成功", FAILED: "失败", TIMEOUT: "超时", CANCELLED: "已取消", LOGGED_IN: "已登录", UNKNOWN: "未知",
};
const label = (value?: string | null) => stateLabel[value || "UNKNOWN"] || value || "未知";

function State({ value }: { value?: string | null }) {
  const text = value || "UNKNOWN";
  return <span className={`state state-${text.toLowerCase()}`}>{label(text)}</span>;
}

export function ControlCenterPage() {
  const [data, setData] = useState<ControlOverview | null>(null);
  const [detail, setDetail] = useState<ControlProfileDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = async (initial = false) => {
    if (initial) setLoading(true); else setRefreshing(true);
    try { setData(await apiClient<ControlOverview>("/control/overview?recent_limit=20")); setError(""); }
    catch (exc) { setError(errorText(exc)); }
    finally { setLoading(false); setRefreshing(false); }
  };

  useEffect(() => {
    void load(true);
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer);
  }, []);

  const openProfile = async (profileRecordId: string) => {
    try { setDetail(await apiClient<ControlProfileDetail>(`/control/profiles/${profileRecordId}`)); }
    catch (exc) { setError(errorText(exc)); }
  };

  if (loading && !data) return <div className="loading">正在加载统一控制中心…</div>;
  if (error && !data) return <><div className="page-title"><div><h1>统一控制中心</h1></div></div><div className="alert error">{error}</div></>;
  if (!data) return null;
  const s = data.summary;
  return <>
    <div className="page-title">
      <div><h1>统一控制中心</h1><p className="muted">统一查看 Workspace、Agent、Profile、Account、Task、Script 和 AI 状态 · {fmt(data.generated_at)}</p></div>
      <button onClick={() => void load()} disabled={refreshing}>{refreshing ? "刷新中…" : "刷新状态"}</button>
    </div>
    {error && <div className="alert error">{error}</div>}
    <div className="metrics control-metrics">
      <div className="metric-card"><span>工作区</span><strong>{s.workspace_count}</strong></div>
      <div className="metric-card good"><span>在线运行端</span><strong>{s.online_agents}/{s.agent_count}</strong></div>
      <div className="metric-card"><span>浏览器环境</span><strong>{s.profile_count}</strong></div>
      <div className="metric-card good"><span>已登录账号</span><strong>{s.logged_in_accounts}/{s.account_count}</strong></div>
      <div className="metric-card"><span>等待 / 运行任务</span><strong>{s.pending_tasks} / {s.running_tasks}</strong></div>
      <div className="metric-card good"><span>成功任务</span><strong>{s.success_tasks}</strong></div>
      <div className="metric-card bad"><span>失败 / 超时任务</span><strong>{s.failed_tasks}</strong></div>
      <div className="metric-card"><span>AI Token</span><strong>{numberText(s.ai_total_tokens)}</strong></div>
    </div>

    <div className="control-stat-strip">
      <span>脚本：<strong>{s.enabled_scripts}/{s.script_count}</strong> 启用</span>
      <span>AI Provider：<strong>{s.enabled_providers}</strong> 个启用</span>
      <span>AI 请求：<strong>{s.ai_request_count}</strong></span>
      <span>分析：<strong>{s.analysis_count}</strong></span>
      <span>话术：<strong>{s.writing_count}</strong></span>
      <span>生图：<strong>{s.image_count}</strong></span>
    </div>

    <section className="panel"><div className="section-heading"><h2>运行端</h2><span className="muted">当前数据范围：{data.scope === "global" ? "全部工作区" : "当前工作区"}</span></div>
      {data.agents.length ? <div className="control-agent-grid">{data.agents.map((agent) => <article className="control-agent-card" key={agent.agent_id}>
        <div className="control-card-head"><div><strong>{agent.agent_name}</strong><small>{agent.machine_name}</small></div><State value={agent.status} /></div>
        <div className="control-card-stats"><span>Profile <b>{agent.profile_total}</b></span><span>等待 <b>{agent.pending_tasks}</b></span><span>运行 <b>{agent.running_tasks}</b></span></div>
        <small className="muted">最近心跳：{fmt(agent.last_heartbeat)} · v{agent.client_version || "-"}</small>
      </article>)}</div> : <div className="empty">暂无运行端</div>}
    </section>

    <section className="panel"><div className="section-heading"><h2>Profile / Account</h2><span className="muted">点击一行查看任务和活动详情</span></div>
      {data.profiles.length ? <div className="table-wrap"><table><thead><tr><th>Profile</th><th>账号</th><th>今日执行</th><th>Agent</th><th>浏览器</th><th>登录</th><th>当前任务</th><th>最近状态</th></tr></thead><tbody>{data.profiles.map((profile) => <tr className="clickable" key={profile.profile_record_id} onClick={() => void openProfile(profile.profile_record_id)}>
        <td><strong>{profile.x_username || profile.profile_id}</strong><div className="mono">{profile.profile_id}</div></td>
        <td>{profile.x_username || "-"}<div className="muted">{profile.account_status || "UNKNOWN"}</div></td>
        <td><strong>赞 {numberText(profile.today_metrics?.likes)} · 关 {numberText(profile.today_metrics?.follows)} · 评 {numberText(profile.today_metrics?.comments)}</strong><div className="muted">扫描 {numberText(profile.today_metrics?.scanned_posts)} · {numberText(profile.today_metrics?.automation_runs)} 次</div></td>
        <td>{profile.agent_name || profile.agent_id.slice(0, 10)}</td>
        <td><State value={profile.browser_status} /></td>
        <td><State value={profile.login_status} /></td>
        <td>{profile.current_task ? <><State value={profile.current_task.status} /><div className="mono">{profile.current_task.task_id.slice(0, 10)}</div></> : "-"}</td>
        <td>{profile.task_count} 个任务</td>
      </tr>)}</tbody></table></div> : <div className="empty">暂无 Profile</div>}
    </section>

    <div className="grid-2 control-lower-grid">
      <section className="panel"><h2>最近任务</h2>{data.recent_tasks.length ? <div className="control-event-list">{data.recent_tasks.map((task) => <div className="control-event" key={task.task_id}><div><strong>{task.script_name || task.task_type}</strong><small>{task.profile_id} · {fmt(task.created_at)}</small></div><State value={task.status} /></div>)}</div> : <div className="empty">暂无任务</div>}</section>
      <section className="panel"><h2>最近活动</h2>{data.recent_activities.length ? <div className="control-event-list">{data.recent_activities.map((activity) => <div className="control-event" key={activity.activity_id}><div><strong>{activity.summary || activity.activity_type}</strong><small>{activity.profile_id} · {fmt(activity.timestamp)}</small></div><State value={activity.status} /></div>)}</div> : <div className="empty">暂无活动</div>}</section>
    </div>

    <section className="panel"><h2>最近审计</h2>{data.recent_audits.length ? <div className="table-wrap"><table><thead><tr><th>时间</th><th>动作</th><th>结果</th><th>资源</th><th>消息</th></tr></thead><tbody>{data.recent_audits.map((item) => <tr key={item.audit_id}><td>{fmt(item.timestamp)}</td><td>{item.action}</td><td><State value={item.result} /></td><td className="mono">{item.resource_type} / {item.resource_id.slice(0, 12)}</td><td>{item.message || "-"}</td></tr>)}</tbody></table></div> : <div className="empty">暂无审计记录</div>}</section>

    {detail && <div className="control-detail-backdrop" onClick={() => setDetail(null)}><section className="panel control-detail" onClick={(event) => event.stopPropagation()}>
      <div className="section-heading"><div><h2>{detail.profile.x_username || detail.profile.profile_id}</h2><p className="muted">{detail.profile.profile_id} · {detail.agent?.agent_name || detail.profile.agent_id}</p></div><button onClick={() => setDetail(null)}>关闭</button></div>
      <div className="control-detail-summary"><span>浏览器 <State value={detail.profile.browser_status} /></span><span>登录 <State value={detail.profile.login_status} /></span><span>账号 <State value={detail.profile.account_status} /></span><span>任务 <strong>{detail.tasks.length}</strong></span></div>
      <h3>今日自动化执行</h3><div className="control-stat-strip"><span>运行 <strong>{numberText(detail.today_metrics.automation_runs)}</strong></span><span>点赞 <strong>{numberText(detail.today_metrics.likes)}</strong></span><span>关注 <strong>{numberText(detail.today_metrics.follows)}</strong></span><span>评论 <strong>{numberText(detail.today_metrics.comments)}</strong></span><span>扫描帖子 <strong>{numberText(detail.today_metrics.scanned_posts)}</strong></span></div>
      <h3>任务历史</h3>{detail.tasks.length ? <div className="control-event-list">{detail.tasks.slice(0, 20).map((task) => <div className="control-event" key={task.task_id}><div><strong>{task.script_name || task.task_type}</strong><small>{fmt(task.created_at)} · {task.error || task.task_id}</small></div><State value={task.status} /></div>)}</div> : <div className="empty">暂无任务</div>}
      <h3>活动历史</h3>{detail.activities.length ? <div className="control-event-list">{detail.activities.slice(0, 20).map((activity) => <div className="control-event" key={activity.activity_id}><div><strong>{activity.summary}</strong><small>{fmt(activity.timestamp)}</small></div><State value={activity.status} /></div>)}</div> : <div className="empty">暂无活动</div>}
    </section></div>}
  </>;
}
