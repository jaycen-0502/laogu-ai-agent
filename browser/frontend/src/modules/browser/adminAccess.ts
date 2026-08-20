import { getBindings } from "./automationScriptApi.shared";

// 当前发行版关闭自动化脚本界面的管理员密码门禁。
const AUTOMATION_ADMIN_PASSWORD_ENABLED = false;
let automationAdminUnlocked = !AUTOMATION_ADMIN_PASSWORD_ENABLED;

export function isAutomationAdminUnlocked(): boolean {
  return !AUTOMATION_ADMIN_PASSWORD_ENABLED || automationAdminUnlocked;
}

export async function unlockAutomationAdmin(
  password: string,
): Promise<boolean> {
  if (!AUTOMATION_ADMIN_PASSWORD_ENABLED) {
    automationAdminUnlocked = true;
    return true;
  }
  const bindings: any = await getBindings();
  const generated = bindings?.VerifyAdminPassword;
  if (typeof generated === "function") {
    automationAdminUnlocked = (await generated(password)) === true;
    return automationAdminUnlocked;
  }

  const runtime = (window as any).go?.main?.App?.VerifyAdminPassword;
  if (typeof runtime === "function") {
    automationAdminUnlocked = (await runtime(password)) === true;
    return automationAdminUnlocked;
  }
  throw new Error("管理员验证接口不可用");
}
