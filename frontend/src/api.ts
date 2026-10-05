let token = "";
export const setToken = (t: string) => { token = t; };
export class ApiError extends Error { constructor(public status: number, public detail: any) { super(String(status)); } }
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as any) };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (typeof init.body === "string") headers["Content-Type"] = "application/json";
  const r = await fetch(`/api${path}`, { ...init, headers });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, body.detail ?? body);
  return body as T;
}
