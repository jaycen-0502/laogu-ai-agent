export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const TOKEN_KEY = "laogu_web_jwt";

export const authStore = {
  get: () => sessionStorage.getItem(TOKEN_KEY) || "",
  set: (token: string) => sessionStorage.setItem(TOKEN_KEY, token),
  clear: () => sessionStorage.removeItem(TOKEN_KEY),
};

export async function apiClient<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 15000,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body && !headers.has("Content-Type"))
    headers.set("Content-Type", "application/json");
  const token = authStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers,
      signal: init.signal || AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new ApiError(0, "服务器连接失败，请检查网络或服务状态");
  }
  if (response.status === 401) {
    authStore.clear();
    if (window.location.pathname !== "/login") window.location.assign("/login");
    throw new ApiError(401, "登录已过期，请重新登录");
  }
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    /* empty body */
  }
  if (!response.ok) {
    const detail =
      typeof payload === "object" && payload && "detail" in payload
        ? String((payload as { detail: unknown }).detail)
        : "请求失败";
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export async function apiBlob(path: string, timeoutMs = 30000): Promise<Blob> {
  const headers = new Headers({ Accept: "image/png,image/jpeg,image/webp" });
  const token = authStore.get();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      headers,
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch {
    throw new ApiError(0, "服务器连接失败，请检查网络或服务状态");
  }
  if (response.status === 401) {
    authStore.clear();
    if (window.location.pathname !== "/login") window.location.assign("/login");
    throw new ApiError(401, "登录已过期，请重新登录");
  }
  if (!response.ok) {
    let detail = "图片读取失败";
    try {
      const payload = await response.json();
      if (payload && typeof payload === "object" && "detail" in payload)
        detail = String((payload as { detail: unknown }).detail);
    } catch {
      /* non-JSON response */
    }
    throw new ApiError(response.status, detail);
  }
  return response.blob();
}

export const jsonBody = (value: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(value),
});
