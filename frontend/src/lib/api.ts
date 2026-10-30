export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.detail || body.error || `请求失败：${response.status}`);
  }
  return body as T;
}

export function sseUrl(path: string, params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  return `${path}?${search.toString()}`;
}

export function prepareAttraction(poiId: string) {
  return fetchJson<{ poi_id: string; job_id: string; status: string }>(
    `/api/attractions/${encodeURIComponent(poiId)}/prepare`,
    { method: "POST" },
  );
}

export function fetchCurrentUser() {
  return fetchJson<{ user: import("./types").UserProfile | null }>("/api/auth/me");
}

export function registerAccount(payload: {
  username: string;
  password: string;
  invite_code: string;
  style_preference: "landscape" | "culture" | "balanced";
}) {
  return fetchJson<{ user: import("./types").UserProfile }>("/api/auth/register", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function loginAccount(payload: { username: string; password: string }) {
  return fetchJson<{ user: import("./types").UserProfile }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function logoutAccount() {
  return fetchJson<{ ok: boolean }>("/api/auth/logout", { method: "POST" });
}

export function updateProfileStyle(style_preference: "landscape" | "culture" | "balanced") {
  return fetchJson<{ user: import("./types").UserProfile }>("/api/profile", {
    method: "PATCH",
    body: JSON.stringify({ style_preference }),
  });
}

export function fetchSupportThreads() {
  return fetchJson<{ threads: import("./types").SupportThread[] }>("/api/support/threads");
}

export function fetchSupportMessages(threadId: string) {
  return fetchJson<{ thread_id: string; messages: import("./types").SupportMessage[] }>(
    `/api/support/threads/${encodeURIComponent(threadId)}`,
  );
}
