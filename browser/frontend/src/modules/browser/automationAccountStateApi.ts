import { getBindings } from "./automationScriptApi.shared";

export interface AutomationAccountState {
  profileId: string;
  platform: string;
  scriptId: string;
  keywords: string[];
  cursor: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
}

function normalizeState(value: any): AutomationAccountState {
  return {
    profileId: String(value?.profileId || ""),
    platform: String(value?.platform || "generic"),
    scriptId: String(value?.scriptId || "default"),
    keywords: Array.isArray(value?.keywords)
      ? value.keywords.map((item: unknown) => String(item || "").trim()).filter(Boolean)
      : [],
    cursor:
      value?.cursor && typeof value.cursor === "object" && !Array.isArray(value.cursor)
        ? value.cursor
        : {},
    createdAt: String(value?.createdAt || ""),
    updatedAt: String(value?.updatedAt || ""),
  };
}

async function resolveAppBindings(): Promise<{ generated: any; runtime: any }> {
  const bindings: any = await getBindings();
  return {
    generated: bindings,
    runtime: (window as any).go?.main?.App || null,
  };
}

export async function fetchAutomationAccountState(
  profileId: string,
  platform: string,
  scriptId: string,
): Promise<AutomationAccountState> {
  const { generated, runtime } = await resolveAppBindings();
  const call = generated?.AutomationAccountStateGet || runtime?.AutomationAccountStateGet;
  if (typeof call !== "function") {
    throw new Error("账号自动化状态接口不可用");
  }
  return normalizeState(
    await call(profileId, platform, scriptId),
  );
}

export async function saveAutomationAccountKeywords(
  profileId: string,
  platform: string,
  scriptId: string,
  keywords: string[],
): Promise<AutomationAccountState> {
  const { generated, runtime } = await resolveAppBindings();
  const call = generated?.AutomationAccountKeywordsSave || runtime?.AutomationAccountKeywordsSave;
  if (typeof call !== "function") {
    throw new Error("账号关键词保存接口不可用");
  }
  return normalizeState(
    await call(profileId, platform, scriptId, keywords),
  );
}
