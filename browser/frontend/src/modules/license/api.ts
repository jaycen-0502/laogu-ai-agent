export interface LicenseStatus {
  licensed: boolean;
  state: string;
  message: string;
  deviceId: string;
  requestCode: string;
  licenseId: string;
  customer: string;
  issuedAt: string;
  expiresAt: string;
  remainingDays: number;
  features: string[];
  clockRollback: boolean;
  remoteState: string;
  lastOnlineCheckAt: string;
  offlineGraceDays: number;
}

function runtimeApp(): any {
  return (window as any).go?.main?.App;
}

export async function fetchLicenseStatus(): Promise<LicenseStatus> {
  const app = runtimeApp();
  if (typeof app?.LicenseStatus !== "function") {
    throw new Error("授权接口尚未准备完成");
  }
  return app.LicenseStatus();
}

export async function regenerateLicenseRequest(): Promise<LicenseStatus> {
  const app = runtimeApp();
  if (typeof app?.LicenseRequestCode !== "function") {
    throw new Error("授权接口尚未准备完成");
  }
  return app.LicenseRequestCode(true);
}

export async function activateLicense(code: string): Promise<LicenseStatus> {
  const app = runtimeApp();
  if (typeof app?.LicenseActivate !== "function") {
    throw new Error("授权接口尚未准备完成");
  }
  return app.LicenseActivate(code);
}
