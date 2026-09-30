let csrf = "";
let mode = location.pathname.startsWith("/demo") ? "demo" : "owner";
export function setMode(value: "owner" | "demo") {
  mode = value;
}
export function setCsrf(token: string) {
  csrf = token;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T = Record<string, unknown>>(
  path: string,
  method = "GET",
  data?: unknown,
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: "include",
    headers: {
      ...(mode === "demo" ? { "X-Qianyan-Mode": "demo" } : {}),
      ...(data !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(method !== "GET" ? { "X-CSRF-Token": csrf } : {}),
    },
    body: data !== undefined ? JSON.stringify(data) : undefined,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new ApiError(
      response.status,
      typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((x: { msg: string }) => x.msg).join("; ")
          : `Request failed (${response.status})`,
    );
  }
  return body as T;
}
