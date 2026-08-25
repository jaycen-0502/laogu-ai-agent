import { useState } from "react";
import { ApiError, apiClient, jsonBody } from "../api/client";

const LANGUAGES = [
  ["auto", "自动识别"],
  ["zh-CN", "简体中文"],
  ["zh-TW", "繁体中文"],
  ["en", "English"],
  ["ja", "日本語"],
  ["ko", "한국어"],
  ["fr", "Français"],
  ["de", "Deutsch"],
  ["es", "Español"],
  ["ru", "Русский"],
  ["pt-BR", "Português (Brasil)"],
] as const;

const TARGET_LANGUAGES = LANGUAGES.filter(([value]) => value !== "auto");

type TranslationResult = {
  translated_text: string;
  source_language: string;
  target_language: string;
  provider_name: string;
  model: string;
  usage: { total_tokens: number; latency_ms: number };
};

const errorText = (error: unknown) =>
  error instanceof ApiError ? error.message : "翻译请求失败，请稍后重试";

export function AITranslationPage() {
  const [text, setText] = useState("");
  const [sourceLanguage, setSourceLanguage] = useState("auto");
  const [targetLanguage, setTargetLanguage] = useState("zh-CN");
  const [result, setResult] = useState<TranslationResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const translate = async () => {
    if (!text.trim()) {
      setError("请输入需要翻译的内容");
      return;
    }
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const next = await apiClient<TranslationResult>(
        "/ai/translate",
        jsonBody({
          text,
          source_language: sourceLanguage,
          target_language: targetLanguage,
        }),
      );
      setResult(next);
      setMessage("翻译完成");
    } catch (exc) {
      setError(errorText(exc));
    } finally {
      setBusy(false);
    }
  };

  const swapLanguages = () => {
    if (sourceLanguage === "auto") {
      setSourceLanguage(targetLanguage);
      setTargetLanguage("en");
      return;
    }
    setSourceLanguage(targetLanguage);
    setTargetLanguage(sourceLanguage);
  };

  return (
    <>
      <div className="page-title">
        <div>
          <h1>AI 翻译</h1>
          <p className="muted">支持多语言翻译，使用管理员为当前账号分配的 Provider 和模型。</p>
        </div>
      </div>

      <div className="alert">翻译内容不会保存到历史记录；系统只记录语言、字符数、模型和耗时，不记录正文。</div>
      {error && <div className="alert error">{error}</div>}
      {message && <div className="alert">{message}</div>}

      <section className="panel translation-panel">
        <div className="translation-toolbar">
          <label>
            原文语言
            <select value={sourceLanguage} disabled={busy} onChange={(event) => setSourceLanguage(event.target.value)}>
              {LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <button type="button" onClick={swapLanguages} disabled={busy}>交换语言</button>
          <label>
            目标语言
            <select value={targetLanguage} disabled={busy} onChange={(event) => setTargetLanguage(event.target.value)}>
              {TARGET_LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>

        <div className="translation-columns">
          <label>
            原文
            <textarea
              rows={14}
              maxLength={20000}
              value={text}
              disabled={busy}
              placeholder="输入需要翻译的内容，最多 20,000 字符"
              onChange={(event) => setText(event.target.value)}
            />
            <span className="muted">{text.length} / 20,000</span>
          </label>
          <label>
            翻译结果
            <textarea rows={14} readOnly value={result?.translated_text || ""} placeholder="翻译结果会显示在这里" />
            {result && <span className="muted">{result.provider_name} · {result.model} · {result.usage.total_tokens} tokens · {result.usage.latency_ms} ms</span>}
          </label>
        </div>

        <div className="toolbar compact">
          <button className="primary" type="button" disabled={busy || !text.trim()} onClick={() => void translate()}>
            {busy ? "翻译中…" : "开始翻译"}
          </button>
          <button type="button" disabled={!result?.translated_text} onClick={() => result && void navigator.clipboard.writeText(result.translated_text)}>复制结果</button>
          <button type="button" disabled={busy && !text} onClick={() => { setText(""); setResult(null); setMessage(""); setError(""); }}>清空</button>
        </div>
      </section>
    </>
  );
}
