import React, { useEffect, useState } from "react";
import { apiClient, ApiError, jsonBody } from "../api/client";
import type { User } from "../types";

const errorText = (value: unknown) =>
  value instanceof ApiError ? value.message : "请求失败，请稍后重试";

type RelayConfig = {
  base_url: string;
  api_key: string;
  api_key_masked?: string;
  default_model: string;
  fallback_model: string;
  timeout: number;
};

type ModelItem = {
  id: number;
  model_name: string;
  model_code: string;
  provider: string;
  category: string;
  status: number;
};

type PingResult = {
  code: number;
  status: string;
  model_tested: string;
  latency: string;
  sample_response: string;
};

export function AIRoutingPage({ user }: { user: User }) {
  // --- 通道配置 ---
  const [relayConfig, setRelayConfig] = useState<RelayConfig>({
    base_url: "https://api.openai.com/v1",
    api_key: "",
    default_model: "gpt-4o-mini",
    fallback_model: "deepseek-chat",
    timeout: 15,
  });
  const [apiKeyInput, setApiKeyInput] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [savingConfig, setSavingConfig] = useState(false);

  // --- 模型字典 ---
  const [models, setModels] = useState<ModelItem[]>([]);
  const [newModel, setNewModel] = useState({
    model_name: "",
    model_code: "",
    provider: "OpenAI",
    category: "日常高频破冰",
    status: 1,
  });
  const [addingModel, setAddingModel] = useState(false);

  const [testModelCode, setTestModelCode] = useState("gpt-4o-mini");
  const [pinging, setPinging] = useState(false);
  const [pingResult, setPingResult] = useState<PingResult | null>(null);
  const [pingError, setPingError] = useState("");

  const [detectingModels, setDetectingModels] = useState(false);
  const [detectNotice, setDetectNotice] = useState("");

  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  // 加载数据
  const loadData = async () => {
    try {
      setError("");
      const [cfgRes, modelsRes] = await Promise.all([
        apiClient<{ code: number; data: RelayConfig }>("/admin/config"),
        apiClient<{ code: number; data: ModelItem[] }>("/admin/models"),
      ]);
      setRelayConfig(cfgRes.data);
      setApiKeyInput(cfgRes.data.api_key || "");
      setModels(modelsRes.data || []);
      if (modelsRes.data?.length && !testModelCode) {
        setTestModelCode(modelsRes.data[0].model_code);
      }
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  // 保存通道配置
  const handleSaveConfig = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingConfig(true);
    setMessage("");
    setError("");
    try {
      await apiClient("/admin/config/update", jsonBody({
        base_url: relayConfig.base_url,
        api_key: apiKeyInput,
        default_model: relayConfig.default_model,
        fallback_model: relayConfig.fallback_model,
        timeout: Number(relayConfig.timeout) || 15,
      }));
      setMessage("✅ 全局通道配置与默认/备用模型已热更新，即时生效！");
      await loadData();
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setSavingConfig(false);
    }
  };

  // 一键自动检测/拉取中转站真实可用模型（如同 CC Switch）
  const handleDetectModels = async () => {
    if (!apiKeyInput) {
      alert("请先填写中转站 API Key 再进行检测");
      return;
    }
    setDetectingModels(true);
    setDetectNotice("");
    setMessage("");
    setError("");
    try {
      const res = await apiClient<{ code: number; message: string; data: { models: ModelItem[]; synced_count: number } }>(
        "/admin/models/fetch-remote",
        jsonBody({
          base_url: relayConfig.base_url,
          api_key: apiKeyInput,
          auto_sync_registry: true,
        })
      );
      const fetched = res.data?.models || [];
      if (fetched.length > 0) {
        setDetectNotice(`✅ 成功探测到 ${fetched.length} 个可用模型！已自动写入可选候选列表与模型库`);
        const firstCode = fetched[0].model_code;
        setRelayConfig((prev) => ({
          ...prev,
          default_model: firstCode,
        }));
        setTestModelCode(firstCode);
        const modelsRes = await apiClient<{ code: number; data: ModelItem[] }>("/admin/models");
        setModels(modelsRes.data || []);
      } else {
        setDetectNotice("⚠️ 未能探测到可用模型，请检查 API Key");
      }
    } catch (exc) {
      setDetectNotice(`❌ 自动检测失败: ${errorText(exc)}`);
    } finally {
      setDetectingModels(false);
    }
  };

  // 测速测活
  const handlePingModel = async () => {
    if (!testModelCode) return;
    setPinging(true);
    setPingResult(null);
    setPingError("");
    try {
      const res = await apiClient<PingResult>("/admin/model/ping", jsonBody({
        test_model_code: testModelCode.trim(),
        base_url: relayConfig.base_url,
        api_key: apiKeyInput,
      }));
      setPingResult(res);
    } catch (exc) {
      setPingError(errorText(exc));
    } finally {
      setPinging(false);
    }
  };

  // 添加模型字典
  const handleAddModel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newModel.model_code) return;
    try {
      await apiClient("/admin/models", jsonBody(newModel));
      setMessage(`✅ 成功添加模型 [${newModel.model_code}] 到字典库！`);
      setAddingModel(false);
      setNewModel({
        model_name: "",
        model_code: "",
        provider: "OpenAI",
        category: "日常高频破冰",
        status: 1,
      });
      await loadData();
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  // 删除模型字典
  const handleDeleteModel = async (id: number) => {
    if (!window.confirm("确定删除该模型字典记录吗？")) return;
    try {
      await apiClient(`/admin/models/${id}`, { method: "DELETE" });
      await loadData();
    } catch (exc) {
      setError(errorText(exc));
    }
  };

  return (
    <div className="page-container" style={{ padding: "20px", maxWidth: "1280px", margin: "0 auto" }}>
      <div style={{ marginBottom: "16px" }}>
        <h1 style={{ fontSize: "24px", fontWeight: "bold", margin: "0 0 6px 0" }}>
          🌐 AI 通道网关与全局模型版本管理
        </h1>
        <p style={{ margin: 0, color: "#666", fontSize: "14px" }}>
          Web 后台专精于中转站 BaseURL、API Key 热更新、可用模型字典库维护及单模型在线测速（推特账号人设与聊天数据 100% 留存在本地客户端）。
        </p>
      </div>

      {message && (
        <div className="alert success" style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "6px" }}>
          {message}
        </div>
      )}
      {error && (
        <div className="alert error" style={{ marginBottom: "16px", padding: "10px 14px", borderRadius: "6px" }}>
          {error}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: "20px" }}>
        {/* 左侧：通道与降级设置 */}
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "8px", border: "1px solid #e0e0e0" }}>
          <h2 style={{ fontSize: "18px", marginTop: 0, marginBottom: "16px", borderBottom: "1px solid #eee", paddingBottom: "10px" }}>
            ⚙️ 中转站通道与容灾降级热配置
          </h2>
          <form onSubmit={handleSaveConfig}>
            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontWeight: "bold", marginBottom: "6px", fontSize: "13px" }}>
                中转站 API 地址 (Base URL)
              </label>
              <input
                type="text"
                style={{ width: "100%", padding: "8px 12px", borderRadius: "4px", border: "1px solid #ccc" }}
                value={relayConfig.base_url}
                onChange={(e) => setRelayConfig({ ...relayConfig, base_url: e.target.value })}
                placeholder="https://api.your-relay.com/v1"
                required
              />
              <small style={{ color: "#888", display: "block", marginTop: "4px" }}>
                支持任意 OpenAI 兼容中转站网关 (末尾 /v1 会自动规范化)
              </small>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label style={{ display: "block", fontWeight: "bold", marginBottom: "6px", fontSize: "13px" }}>
                中转站 API Key
              </label>
              <div style={{ display: "flex", gap: "8px" }}>
                <input
                  type={showApiKey ? "text" : "password"}
                  style={{ flex: 1, padding: "8px 12px", borderRadius: "4px", border: "1px solid #ccc" }}
                  value={apiKeyInput}
                  onChange={(e) => setApiKeyInput(e.target.value)}
                  placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxx"
                  required
                />
                <button
                  type="button"
                  className="secondary"
                  onClick={() => setShowApiKey(!showApiKey)}
                  style={{ padding: "0 12px", whiteSpace: "nowrap" }}
                >
                  {showApiKey ? "隐藏" : "显示"}
                </button>
              </div>
            </div>

            {/* 一键自动检测/拉取中转站可用模型 (如同 CC Switch) */}
            <div style={{ marginBottom: "16px", padding: "10px 14px", background: "#f8fafc", border: "1px solid #cbd5e1", borderRadius: "6px" }}>
              <div style={{ display: "flex", gap: "10px", alignItems: "center", flexWrap: "wrap" }}>
                <button
                  type="button"
                  className="secondary"
                  onClick={handleDetectModels}
                  disabled={detectingModels || !apiKeyInput}
                  style={{
                    background: "#ecfdf5",
                    border: "1px solid #10b981",
                    color: "#047857",
                    fontWeight: "bold",
                    fontSize: "13px",
                    padding: "7px 14px",
                    borderRadius: "5px",
                    cursor: "pointer",
                  }}
                >
                  {detectingModels ? "🔍 正在自动检测拉取可用模型…" : "🔍 一键自动检测可用模型 (如 CC Switch)"}
                </button>
                <span style={{ fontSize: "12px", color: "#64748b" }}>
                  自动调用中转站 /v1/models 接口，拉取真实开通的模型并写入候选列表
                </span>
              </div>
              {detectNotice && (
                <div style={{ marginTop: "8px", fontSize: "12px", color: detectNotice.startsWith("❌") ? "#dc2626" : "#16a34a", fontWeight: "bold" }}>
                  {detectNotice}
                </div>
              )}
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "14px" }}>
              <div>
                <label style={{ display: "block", fontWeight: "bold", marginBottom: "6px", fontSize: "13px" }}>
                  🎯 全局主调用默认模型
                </label>
                <input
                  type="text"
                  list="registered_models_list"
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "4px", border: "1px solid #ccc" }}
                  value={relayConfig.default_model}
                  onChange={(e) => setRelayConfig({ ...relayConfig, default_model: e.target.value })}
                  placeholder="gpt-4o-mini"
                  required
                />
                <small style={{ color: "#888", display: "block", marginTop: "4px" }}>
                  未在本地单独绑定模型的账号默认使用此模型
                </small>
              </div>

              <div>
                <label style={{ display: "block", fontWeight: "bold", marginBottom: "6px", fontSize: "13px", color: "#c65102" }}>
                  🛡️ 应急降级备用模型 (Fallback)
                </label>
                <input
                  type="text"
                  list="registered_models_list"
                  style={{ width: "100%", padding: "8px 12px", borderRadius: "4px", border: "1px solid #ccc" }}
                  value={relayConfig.fallback_model}
                  onChange={(e) => setRelayConfig({ ...relayConfig, fallback_model: e.target.value })}
                  placeholder="deepseek-chat"
                  required
                />
                <small style={{ color: "#888", display: "block", marginTop: "4px" }}>
                  主模型报错/限流/超时时秒级自动切入
                </small>
              </div>
            </div>

            <datalist id="registered_models_list">
              {models.map((m) => (
                <option key={m.id} value={m.model_code}>
                  {m.model_name} ({m.provider})
                </option>
              ))}
            </datalist>

            <div style={{ marginBottom: "18px" }}>
              <label style={{ display: "block", fontWeight: "bold", marginBottom: "6px", fontSize: "13px" }}>
                请求超时时间 (秒)
              </label>
              <input
                type="number"
                min="5"
                max="120"
                style={{ width: "140px", padding: "8px 12px", borderRadius: "4px", border: "1px solid #ccc" }}
                value={relayConfig.timeout}
                onChange={(e) => setRelayConfig({ ...relayConfig, timeout: Number(e.target.value) })}
              />
            </div>

            <button type="submit" className="primary" disabled={savingConfig} style={{ width: "100%", padding: "10px" }}>
              {savingConfig ? "正在热更新配置…" : "💾 保存通道配置并热更新生效"}
            </button>
          </form>
        </div>

        {/* 右侧：单模型即时测速与字典库 */}
        <div className="card" style={{ background: "#fff", padding: "20px", borderRadius: "8px", border: "1px solid #e0e0e0" }}>
          <h2 style={{ fontSize: "18px", marginTop: 0, marginBottom: "16px", borderBottom: "1px solid #eee", paddingBottom: "10px" }}>
            ⚡ 单模型在线即时测速与测活
          </h2>
          <div style={{ marginBottom: "14px" }}>
            <label style={{ display: "block", fontWeight: "bold", marginBottom: "6px", fontSize: "13px" }}>
              输入或选择待测试模型代码
            </label>
            <div style={{ display: "flex", gap: "8px" }}>
              <input
                type="text"
                list="registered_models_list"
                style={{ flex: 1, padding: "8px 12px", borderRadius: "4px", border: "1px solid #ccc" }}
                value={testModelCode}
                onChange={(e) => setTestModelCode(e.target.value)}
                placeholder="例如: claude-3-5-sonnet-20241022"
              />
              <button
                type="button"
                className="primary"
                onClick={handlePingModel}
                disabled={pinging || !testModelCode}
                style={{ padding: "8px 16px", whiteSpace: "nowrap" }}
              >
                {pinging ? "测速中…" : "Ping 测试"}
              </button>
            </div>
          </div>

          {pingResult && (
            <div style={{ background: "#f0fdf4", border: "1px solid #bbf7d0", padding: "12px", borderRadius: "6px", marginTop: "12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "6px" }}>
                <span style={{ fontWeight: "bold", color: "#166534" }}>🟢 状态: 在线正常 (Online)</span>
                <span style={{ fontWeight: "bold", color: "#15803d" }}>延迟: {pingResult.latency}</span>
              </div>
              <div style={{ fontSize: "12px", color: "#4b5563" }}>
                <strong>测试模型:</strong> {pingResult.model_tested}
              </div>
              <div style={{ fontSize: "12px", color: "#4b5563", marginTop: "4px" }}>
                <strong>响应样例:</strong> {pingResult.sample_response || "(空)"}
              </div>
            </div>
          )}

          {pingError && (
            <div style={{ background: "#fef2f2", border: "1px solid #fecaca", padding: "12px", borderRadius: "6px", marginTop: "12px", color: "#991b1b", fontSize: "13px" }}>
              ❌ <strong>调用失败:</strong> {pingError}
            </div>
          )}

          {/* 模型字典列表 */}
          <div style={{ marginTop: "20px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
              <h3 style={{ fontSize: "14px", fontWeight: "bold", margin: 0 }}>📚 全局可用模型字典库</h3>
              <button
                className="secondary"
                onClick={() => setAddingModel(!addingModel)}
                style={{ fontSize: "12px", padding: "4px 8px" }}
              >
                {addingModel ? "取消添加" : "+ 录入新模型"}
              </button>
            </div>

            {addingModel && (
              <form onSubmit={handleAddModel} style={{ background: "#f9fafb", padding: "12px", borderRadius: "6px", marginBottom: "12px", border: "1px solid #eee" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "8px" }}>
                  <input
                    placeholder="展示名称 (如 Claude 3.7)"
                    value={newModel.model_name}
                    onChange={(e) => setNewModel({ ...newModel, model_name: e.target.value })}
                    required
                    style={{ padding: "6px", fontSize: "12px" }}
                  />
                  <input
                    placeholder="模型代码 (如 claude-3-7-sonnet)"
                    value={newModel.model_code}
                    onChange={(e) => setNewModel({ ...newModel, model_code: e.target.value })}
                    required
                    style={{ padding: "6px", fontSize: "12px" }}
                  />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", marginBottom: "8px" }}>
                  <input
                    placeholder="厂商归属 (如 Anthropic)"
                    value={newModel.provider}
                    onChange={(e) => setNewModel({ ...newModel, provider: e.target.value })}
                    style={{ padding: "6px", fontSize: "12px" }}
                  />
                  <input
                    placeholder="推荐场景 (如 高价值大号)"
                    value={newModel.category}
                    onChange={(e) => setNewModel({ ...newModel, category: e.target.value })}
                    style={{ padding: "6px", fontSize: "12px" }}
                  />
                </div>
                <button type="submit" className="primary" style={{ width: "100%", padding: "6px", fontSize: "12px" }}>
                  确认录入字典
                </button>
              </form>
            )}

            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
              <thead>
                <tr style={{ background: "#f3f4f6", textAlign: "left" }}>
                  <th style={{ padding: "6px" }}>模型代码</th>
                  <th style={{ padding: "6px" }}>厂商</th>
                  <th style={{ padding: "6px" }}>推荐场景</th>
                  <th style={{ padding: "6px", width: "40px" }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {models.map((m) => (
                  <tr key={m.id} style={{ borderBottom: "1px solid #eee" }}>
                    <td style={{ padding: "6px", fontWeight: "bold" }}>
                      <a
                        href="#ping"
                        onClick={(e) => {
                          e.preventDefault();
                          setTestModelCode(m.model_code);
                        }}
                        style={{ color: "#2563eb", textDecoration: "none" }}
                        title="点击填入测速框"
                      >
                        {m.model_code}
                      </a>
                    </td>
                    <td style={{ padding: "6px" }}>{m.provider}</td>
                    <td style={{ padding: "6px", color: "#6b7280" }}>{m.category}</td>
                    <td style={{ padding: "6px" }}>
                      <button
                        onClick={() => handleDeleteModel(m.id)}
                        style={{ border: "none", background: "none", color: "#ef4444", cursor: "pointer", fontSize: "14px" }}
                        title="删除"
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
