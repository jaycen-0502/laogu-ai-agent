import { useCallback, useEffect, useState, type ReactNode } from "react";
import { CheckCircle2, Copy, KeyRound, RefreshCw, ShieldCheck } from "lucide-react";
import { Button, FormItem, Loading, Textarea, toast } from "../../shared/components";
import { ClipboardSetText } from "../../wailsjs/runtime/runtime";
import {
  activateLicense,
  fetchLicenseStatus,
  regenerateLicenseRequest,
  type LicenseStatus,
} from "./api";

interface LicenseGateProps {
  children: ReactNode;
}

export function LicenseGate({ children }: LicenseGateProps) {
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [activationCode, setActivationCode] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [renewOpen, setRenewOpen] = useState(false);
  const [renewRequestCode, setRenewRequestCode] = useState("");

  const remoteStatusText = status?.remoteState === "VALID"
    ? `在线验证正常${status.lastOnlineCheckAt ? ` · 最近检查 ${new Date(status.lastOnlineCheckAt).toLocaleString("zh-CN")}` : ""}`
    : status?.state === "licensed_offline"
      ? `授权服务器暂不可用 · 离线宽限 ${status.offlineGraceDays} 天`
      : "仅使用本机签名授权";

  async function generateRenewRequest() {
    setSubmitting(true);
    try {
      const next = await regenerateLicenseRequest();
      setStatus(next);
      setRenewRequestCode(next.requestCode || "");
      setActivationCode("");
      toast.success("已生成当前电脑的新请求码");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "生成请求码失败");
    } finally {
      setSubmitting(false);
    }
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchLicenseStatus());
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "读取授权状态失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let disposed = false;
    const attempt = async () => {
      for (let index = 0; index < 20 && !disposed; index += 1) {
        try {
          const value = await fetchLicenseStatus();
          if (!disposed) {
            setStatus(value);
            setLoading(false);
          }
          return;
        } catch {
          await new Promise((resolve) => window.setTimeout(resolve, 150));
        }
      }
      if (!disposed) {
        setLoading(false);
        toast.error("授权模块初始化失败，请重新打开软件");
      }
    };
    void attempt();
    return () => {
      disposed = true;
    };
  }, []);

  if (loading && !status) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-bg-base)]">
        <Loading text="正在验证本机授权…" />
      </div>
    );
  }

  if (status?.licensed) {
	return (
	  <>
		{children}
		<button
		  type="button"
		  onClick={() => setRenewOpen(true)}
		  className="fixed bottom-4 left-4 z-[100] rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 shadow-sm"
		>
		  更新授权
		</button>
		{status.remainingDays <= 7 ? (
		  <div className="fixed bottom-4 right-4 z-[100] rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-medium text-amber-800 shadow-sm">
			<div>授权剩余 {status.remainingDays} 天 · {new Date(status.expiresAt).toLocaleDateString("zh-CN")} 到期</div>
			<div className="mt-1 text-[10px] font-normal">{remoteStatusText}</div>
		  </div>
		) : (
		  <div className="fixed bottom-4 right-4 z-[100] rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-800 shadow-sm">
			<div>授权有效 · {new Date(status.expiresAt).toLocaleDateString("zh-CN")} 到期</div>
			<div className="mt-1 text-[10px] font-normal">{remoteStatusText}</div>
		  </div>
		)}
		{renewOpen ? (
		  <div className="fixed inset-0 z-[200] flex items-center justify-center bg-slate-900/40 px-5">
		    <div className="w-full max-w-xl rounded-2xl bg-white p-6 shadow-2xl">
		      <h2 className="text-lg font-semibold text-slate-900">更新远程授权</h2>
		      <p className="mt-2 text-sm text-slate-500">粘贴网页管理后台或授权终端为当前电脑请求码签发的新 LGACT1 激活码。</p>
		      <div className="mt-4 flex items-center justify-between gap-3">
		        <span className="text-xs text-slate-500">{renewRequestCode ? "请求码已生成，请复制到授权终端" : "尚未生成本机请求码"}</span>
		        <Button variant="secondary" size="sm" onClick={generateRenewRequest} loading={submitting}>生成本机请求码</Button>
		      </div>
		      {renewRequestCode ? (
		        <Textarea readOnly rows={4} value={renewRequestCode} className="mt-3 font-mono text-xs leading-5" />
		      ) : null}
		      <Textarea
		        rows={6}
		        value={activationCode}
		        onChange={(event) => setActivationCode(event.target.value)}
		        placeholder="LGACT1...."
		        className="mt-4 font-mono text-xs leading-5"
		      />
		      <div className="mt-4 flex justify-end gap-2">
		        <Button variant="secondary" onClick={() => { setRenewOpen(false); setActivationCode(""); }} disabled={submitting}>取消</Button>
		        <Button onClick={activate} loading={submitting}>应用新授权</Button>
		      </div>
		    </div>
		  </div>
		) : null}
	  </>
	);
  }

  const copyRequest = async () => {
    if (!status?.requestCode) return;
    await ClipboardSetText(status.requestCode);
    toast.success("请求码已复制");
  };

  const regenerate = async () => {
    setSubmitting(true);
    try {
      const next = await regenerateLicenseRequest();
      setStatus(next);
      setActivationCode("");
      toast.success("已生成新的请求码，旧请求码对应的激活码将不能使用");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "生成请求码失败");
    } finally {
      setSubmitting(false);
    }
  };

  async function activate() {
    const code = activationCode.trim();
    if (!code) {
      toast.warning("请先粘贴激活码");
      return;
    }
    setSubmitting(true);
    try {
      const next = await activateLicense(code);
      setStatus(next);
      if (next.licensed) {
        setRenewOpen(false);
        setActivationCode("");
        toast.success(`激活成功，有效期至 ${new Date(next.expiresAt).toLocaleString("zh-CN")}`);
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "激活失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-[var(--color-bg-base)] px-5 py-8">
      <div className="mx-auto flex min-h-[calc(100vh-4rem)] max-w-3xl items-center">
        <div className="w-full overflow-hidden rounded-2xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] shadow-xl shadow-slate-900/5">
          <div className="border-b border-[var(--color-border-muted)] px-7 py-6">
            <div className="flex items-start gap-4">
              <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[var(--color-accent-muted)] text-[var(--color-accent)]">
                <ShieldCheck className="h-6 w-6" />
              </div>
              <div>
                <h1 className="text-xl font-semibold text-[var(--color-text-primary)]">老谷浏览器授权激活</h1>
                <p className="mt-1 text-sm leading-6 text-[var(--color-text-secondary)]">
                  激活码只绑定当前电脑。启用远程授权时，程序会在启动和运行期间检查授权状态；网络暂时不可用时按离线宽限继续使用。
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-6 px-7 py-6">
            <div className="rounded-xl border border-[var(--color-border-muted)] bg-[var(--color-bg-muted)] p-4">
              <div className="mb-2 flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-[var(--color-text-primary)]">第 1 步：发送机器请求码</p>
                  <p className="mt-1 text-xs text-[var(--color-text-muted)]">设备尾号：{status?.deviceId?.slice(-12) || "-"}</p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" size="sm" onClick={regenerate} loading={submitting}>
                    <RefreshCw className="h-3.5 w-3.5" />重新生成
                  </Button>
                  <Button size="sm" onClick={copyRequest} disabled={!status?.requestCode}>
                    <Copy className="h-3.5 w-3.5" />复制请求码
                  </Button>
                </div>
              </div>
              <Textarea
                readOnly
                rows={5}
                value={status?.requestCode || ""}
                className="font-mono text-xs leading-5"
              />
            </div>

            <div>
              <div className="mb-3 flex items-center gap-2">
                <KeyRound className="h-4 w-4 text-[var(--color-accent)]" />
                <p className="text-sm font-medium text-[var(--color-text-primary)]">第 2 步：粘贴收到的激活码</p>
              </div>
              <FormItem error={status?.clockRollback ? status.message : undefined}>
                <Textarea
                  rows={6}
                  value={activationCode}
                  onChange={(event) => setActivationCode(event.target.value)}
                  placeholder="LGACT1.…"
                  className="font-mono text-xs leading-5"
                  error={status?.clockRollback}
                />
              </FormItem>
              {status?.message && !status.clockRollback ? (
                <p className="mt-2 text-xs text-[var(--color-text-muted)]">{status.message}</p>
              ) : null}
            </div>

            <div className="flex items-center justify-between gap-4 border-t border-[var(--color-border-muted)] pt-5">
              <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
                <CheckCircle2 className="h-4 w-4" />激活码无法复制到其他电脑使用
              </div>
              <div className="flex gap-2">
                <Button variant="secondary" onClick={load} disabled={submitting}>刷新状态</Button>
                <Button onClick={activate} loading={submitting}>立即激活</Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
