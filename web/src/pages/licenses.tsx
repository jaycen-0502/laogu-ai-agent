import { useEffect, useState } from "react";
import { apiClient, ApiError, jsonBody } from "../api/client";
import type { License, LicenseCheck, LicenseDevice } from "../types";

const errorText = (value: unknown) => value instanceof ApiError ? value.message : "无法加载授权信息";
const formatDate = (value: string | null) => value ? new Date(value).toLocaleString("zh-CN") : "—";
const stateLabel = (value: string) => ({ ACTIVE: "有效", REVOKED: "已撤销", EXPIRED: "已过期" }[value] || value);

export function LicensesPage() {
  const [items, setItems] = useState<License[]>([]);
  const [selected, setSelected] = useState<License | null>(null);
  const [devices, setDevices] = useState<LicenseDevice[]>([]);
  const [checks, setChecks] = useState<LicenseCheck[]>([]);
  const [reason, setReason] = useState("");
  const [revokeTarget, setRevokeTarget] = useState<License | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [issuerConfigured, setIssuerConfigured] = useState(false);
  const [issueOpen, setIssueOpen] = useState(false);
  const [requestCode, setRequestCode] = useState("");
  const [issueDays, setIssueDays] = useState("30");
  const [issueCustomer, setIssueCustomer] = useState("");
  const [issueLicenseId, setIssueLicenseId] = useState("");
  const [issuedCode, setIssuedCode] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      setItems(await apiClient<License[]>("/license/status"));
      setError("");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setLoading(false);
    }
  };

  const openDetails = async (item: License) => {
    setSelected(item);
    setMessage("");
    try {
      const [nextDevices, nextChecks] = await Promise.all([
        apiClient<LicenseDevice[]>(`/license/${encodeURIComponent(item.license_id)}/devices`),
        apiClient<LicenseCheck[]>(`/license/${encodeURIComponent(item.license_id)}/checks?limit=20`),
      ]);
      setDevices(nextDevices);
      setChecks(nextChecks);
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  const revoke = async () => {
    if (!revokeTarget) return;
    setBusy(true);
    try {
      await apiClient(`/license/revoke`, jsonBody({ license_id: revokeTarget.license_id, reason }));
      setMessage(`授权 ${revokeTarget.license_id} 已撤销`);
      setRevokeTarget(null);
      setReason("");
      await load();
      if (selected?.license_id === revokeTarget.license_id) {
        const refreshed = { ...selected, status: "REVOKED", revoked_at: new Date().toISOString() };
        setSelected(refreshed);
      }
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  const loadIssuerStatus = async () => {
    try {
      const status = await apiClient<{ configured: boolean }>("/license/issuer-status");
      setIssuerConfigured(status.configured);
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  const issue = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await apiClient<{ activation_code: string }>("/license/issue", jsonBody({
        request_code: requestCode.trim(),
        days: Number(issueDays),
        customer: issueCustomer.trim(),
        license_id: issueLicenseId.trim(),
      }));
      setIssuedCode(response.activation_code);
      setMessage("激活码已生成。它只在本次响应中返回，请立即复制并粘贴到对应浏览器。");
      await load();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  const copyIssuedCode = async () => {
    if (!issuedCode) return;
    await navigator.clipboard.writeText(issuedCode);
    setMessage("激活码已复制。不要把它发送到聊天、工单或日志中。");
  };

  useEffect(() => { void load(); void loadIssuerStatus(); }, []);

  return <>
    <div className="page-title">
      <div><h1>远程授权</h1><p className="muted">查看授权有效期、设备和在线检查记录。不会显示私钥。</p></div>
      <div className="row-actions"><button onClick={() => void load()} disabled={loading}>{loading ? "刷新中…" : "刷新"}</button><button className="primary" onClick={() => { setIssueOpen(true); setIssuedCode(""); }} disabled={!issuerConfigured}>在线生成激活码</button></div>
    </div>
    {!issuerConfigured && <div className="alert">在线签发尚未配置。请继续使用授权终端离线签发；配置服务器私钥后此按钮会自动启用。</div>}
    {error && <div className="alert error">{error}</div>}
    {message && <div className="alert">{message}</div>}
    <section className="panel">
      <div className="toolbar"><strong>授权列表</strong><span className="muted">共 {items.length} 个</span></div>
      {loading && !items.length ? <div className="loading">正在加载授权…</div> : !items.length ? <div className="empty">暂无授权记录</div> :
        <div className="table-wrap"><table><thead><tr><th>授权编号</th><th>客户</th><th>状态</th><th>有效期</th><th>设备</th><th>最近检查</th><th>操作</th></tr></thead>
          <tbody>{items.map((item) => <tr key={item.id}>
            <td className="mono">{item.license_id}</td>
            <td>{item.customer || "—"}</td>
            <td><span className={`state state-${item.status.toLowerCase()}`}>{stateLabel(item.status)}</span></td>
            <td>{formatDate(item.issued_at)}<br /><span className="muted">至 {formatDate(item.expires_at)}</span></td>
            <td>{item.device_count}</td>
            <td>{formatDate(item.last_check)}</td>
            <td><div className="row-actions"><button onClick={() => void openDetails(item)}>详情</button>{item.status === "ACTIVE" && <button className="danger-button" onClick={() => setRevokeTarget(item)}>撤销</button>}</div></td>
          </tr>)}</tbody>
        </table></div>}
    </section>
    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><section className="modal-panel" onClick={(event) => event.stopPropagation()}>
      <div className="modal-header"><div><h2>授权详情</h2><p className="muted mono">{selected.license_id}</p></div><button onClick={() => setSelected(null)}>关闭</button></div>
      <div className="license-detail-grid"><div><span>状态</span><strong>{stateLabel(selected.status)}</strong></div><div><span>有效期至</span><strong>{formatDate(selected.expires_at)}</strong></div><div><span>离线宽限</span><strong>{selected.offline_grace_days} 天</strong></div><div><span>设备数量</span><strong>{selected.device_count}</strong></div></div>
      <h3>已登记设备</h3>{devices.length ? <div className="table-wrap"><table><thead><tr><th>设备</th><th>版本</th><th>最后在线</th><th>IP（已打码）</th></tr></thead><tbody>{devices.map((device) => <tr key={device.id}><td className="mono">{device.device_id}</td><td>{device.app_version || "—"}</td><td>{formatDate(device.last_seen_at)}</td><td>{device.last_ip || "—"}</td></tr>)}</tbody></table></div> : <div className="empty">暂无设备在线记录</div>}
      <h3>最近检查</h3>{checks.length ? <div className="table-wrap"><table><thead><tr><th>时间</th><th>结果</th><th>原因</th><th>设备</th></tr></thead><tbody>{checks.map((check) => <tr key={check.id}><td>{formatDate(check.checked_at)}</td><td><span className={`state state-${check.result.toLowerCase()}`}>{check.result}</span></td><td>{check.reason || "—"}</td><td className="mono">{check.device_id}</td></tr>)}</tbody></table></div> : <div className="empty">暂无检查记录</div>}
    </section></div>}
    {revokeTarget && <div className="modal-backdrop" onClick={() => setRevokeTarget(null)}><section className="modal-panel narrow" onClick={(event) => event.stopPropagation()}><h2>撤销授权</h2><p>确定撤销 <span className="mono">{revokeTarget.license_id}</span> 吗？已连接的浏览器将在下一次在线检查时收到撤销状态。</p><label>撤销原因（可选）<textarea rows={3} maxLength={300} value={reason} onChange={(event) => setReason(event.target.value)} /></label><div className="modal-actions"><button onClick={() => setRevokeTarget(null)}>取消</button><button className="danger-button" onClick={() => void revoke()} disabled={busy}>{busy ? "处理中…" : "确认撤销"}</button></div></section></div>}
    {issueOpen && <div className="modal-backdrop" onClick={() => setIssueOpen(false)}><section className="modal-panel narrow" onClick={(event) => event.stopPropagation()}><div className="modal-header"><div><h2>在线生成激活码</h2><p className="muted">仅管理员可用。服务器不会保存完整激活码。</p></div><button onClick={() => setIssueOpen(false)}>关闭</button></div><label>浏览器请求码（LGREQ1）<textarea rows={5} value={requestCode} onChange={(event) => setRequestCode(event.target.value)} placeholder="粘贴浏览器生成的 LGREQ1 请求码" /></label><div className="form-grid"><label>有效天数<input type="number" min={1} max={3650} value={issueDays} onChange={(event) => setIssueDays(event.target.value)} /></label><label>客户备注<input value={issueCustomer} maxLength={200} onChange={(event) => setIssueCustomer(event.target.value)} placeholder="可选" /></label><label>许可证编号<input value={issueLicenseId} maxLength={120} onChange={(event) => setIssueLicenseId(event.target.value)} placeholder="留空自动生成" /></label></div><div className="modal-actions"><button onClick={() => setIssueOpen(false)}>取消</button><button className="primary" onClick={() => void issue()} disabled={busy || requestCode.trim().length < 32}>{busy ? "生成中…" : "生成激活码"}</button></div>{issuedCode && <><h3>本次生成的激活码</h3><textarea rows={7} readOnly value={issuedCode} /><div className="modal-actions"><button className="primary" onClick={() => void copyIssuedCode()}>复制激活码</button></div><p className="muted">复制后粘贴到对应浏览器。关闭窗口后页面不会再次读取这串激活码。</p></>}</section></div>}
  </>;
}
