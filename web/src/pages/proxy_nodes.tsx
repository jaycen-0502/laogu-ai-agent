import { useState } from "react";
import { apiClient, ApiError, jsonBody } from "../api/client";

type FormState = {
  name: string;
  server: string;
  port: string;
  uuid: string;
  udp: boolean;
  tls: boolean;
  network: string;
  servername: string;
  public_key: string;
  short_id: string;
  client_fingerprint: string;
};

const initial: FormState = {
  name: "",
  server: "",
  port: "443",
  uuid: "",
  udp: true,
  tls: true,
  network: "tcp",
  servername: "",
  public_key: "",
  short_id: "",
  client_fingerprint: "chrome",
};

const errorText = (value: unknown) => value instanceof ApiError ? value.message : "转换失败，请稍后重试";

export function ProxyNodesPage() {
  const [form, setForm] = useState<FormState>(initial);
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (key: keyof FormState, value: string | boolean) => setForm((current) => ({ ...current, [key]: value }));

  const convert = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(""); setResult("");
    try {
      const response = await apiClient<{ yaml: string }>("/proxy/convert/vless-reality", jsonBody({
        ...form,
        port: Number(form.port),
      }));
      setResult(response.yaml);
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  return <>
    <div className="page-title">
      <div><h1>代理节点</h1><p className="muted">将 VLESS Reality 参数转换为标准 YAML 模板。转换结果不会写入服务器。</p></div>
      {result && <button type="button" onClick={() => void navigator.clipboard.writeText(result)}>复制 YAML</button>}
    </div>
    {error && <div className="alert error">{error}</div>}
    <div className="grid-2">
      <section className="panel">
        <h2>VLESS Reality 参数</h2>
        <form className="form-grid" onSubmit={convert}>
          <label>节点名称<input required value={form.name} onChange={(event) => set("name", event.target.value)} placeholder="例如：cjh3U备" /></label>
          <label>服务器地址<input required value={form.server} onChange={(event) => set("server", event.target.value)} placeholder="xxh.jaycwl.org" /></label>
          <label>端口<input required type="number" min="1" max="65535" value={form.port} onChange={(event) => set("port", event.target.value)} /></label>
          <label>UUID<input required value={form.uuid} onChange={(event) => set("uuid", event.target.value)} autoComplete="off" /></label>
          <label>Server Name<input required value={form.servername} onChange={(event) => set("servername", event.target.value)} placeholder="download-porter.hoyoverse.com" /></label>
          <label>Public Key<input required value={form.public_key} onChange={(event) => set("public_key", event.target.value)} autoComplete="off" /></label>
          <label>Short ID<input required value={form.short_id} onChange={(event) => set("short_id", event.target.value)} autoComplete="off" /></label>
          <label>传输协议<select value={form.network} onChange={(event) => set("network", event.target.value)}><option value="tcp">TCP</option><option value="ws">WebSocket</option><option value="grpc">gRPC</option><option value="h2">HTTP/2</option></select></label>
          <label>客户端指纹<input value={form.client_fingerprint} onChange={(event) => set("client_fingerprint", event.target.value)} /></label>
          <div className="form-checks"><label><input type="checkbox" checked={form.udp} onChange={(event) => set("udp", event.target.checked)} /> UDP</label><label><input type="checkbox" checked={form.tls} onChange={(event) => set("tls", event.target.checked)} /> TLS</label></div>
          <p className="muted form-help">仅用于格式转换；UUID、Public Key 和 Short ID 不会写入日志或数据库。</p>
          <button className="primary" type="submit" disabled={busy}>{busy ? "转换中…" : "生成 YAML"}</button>
        </form>
      </section>
      <section className="panel">
        <div className="section-heading"><h2>转换预览</h2><span className="muted">Clash / Xray 兼容模板</span></div>
        {result ? <pre className="code-preview"><code>{result}</code></pre> : <div className="empty">填写参数后生成预览</div>}
      </section>
    </div>
  </>;
}
