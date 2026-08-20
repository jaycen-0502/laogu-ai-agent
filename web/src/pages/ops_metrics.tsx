import { useEffect, useState } from "react";
import { apiClient, ApiError } from "../api/client";
import type { OpsMetrics } from "../types";

const errorText = (value: unknown) => value instanceof ApiError ? value.message : "无法加载运维指标";
const fmtUptime = (seconds: number) => {
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return `${hours}小时 ${minutes}分钟`;
};

export function OpsMetricsPage() {
  const [data, setData] = useState<OpsMetrics | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);

  const load = async () => {
    setRefreshing(true);
    try {
      setData(await apiClient<OpsMetrics>("/admin/ops/metrics"));
      setError("");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 15000);
    return () => window.clearInterval(timer);
  }, []);

  if (!data && error) return <><div className="page-title"><div><h1>运维监控</h1><p className="muted">仅管理员可见</p></div></div><div className="alert error">{error}</div></>;
  if (!data) return <div className="loading">正在加载运维指标…</div>;
  const status = data.commands.by_status;
  return <>
    <div className="page-title">
      <div><h1>运维监控</h1><p className="muted">服务、数据库、Agent、Command 和通道状态</p></div>
      <button onClick={() => void load()} disabled={refreshing}>{refreshing ? "刷新中…" : "刷新"}</button>
    </div>
    {error && <div className="alert error">{error}</div>}
    <div className="metrics control-metrics">
      <div className="metric-card good"><span>服务版本</span><strong>{data.service.version}</strong></div>
      <div className={`metric-card ${data.database.reachable ? "good" : "bad"}`}><span>数据库</span><strong>{data.database.reachable ? "正常" : "异常"}</strong></div>
      <div className={`metric-card ${data.agents.online ? "good" : "bad"}`}><span>Agent 在线</span><strong>{data.agents.online}/{data.agents.total}</strong></div>
      <div className="metric-card"><span>服务运行时长</span><strong>{fmtUptime(data.service.uptime_seconds)}</strong></div>
      <div className="metric-card"><span>Command 总数</span><strong>{data.commands.total}</strong></div>
      <div className={`metric-card ${data.commands.stale_delivered ? "bad" : "good"}`}><span>过期租约</span><strong>{data.commands.stale_delivered}</strong></div>
    </div>
    <div className="grid-2">
      <section className="panel"><h2>Command 状态</h2><div className="control-stat-strip">
        {Object.entries(status).map(([key, value]) => <span key={key}>{key}: <strong>{value}</strong></span>)}
      </div><p className="muted">租约时长：{data.commands.lease_seconds} 秒</p></section>
      <section className="panel"><h2>命令通道</h2><div className="control-stat-strip">
        <span>WebSocket：<strong>{data.channels.websocket ? "正常" : "关闭"}</strong></span>
        <span>HTTP Pull fallback：<strong>{data.channels.http_pull_fallback ? "启用" : "关闭"}</strong></span>
      </div><p className="muted">页面每 15 秒自动刷新；不显示任何凭据或 Token。</p></section>
    </div>
  </>;
}
