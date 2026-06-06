import { useAuthStore } from "@/lib/auth/store";
import { ApiError } from "./types";

// Default follows the app's base path so it works both standalone (base "/") and behind the
// HWAX portal sub-path (base "/heax-hub/" → "/heax-hub/api/v1"). Override with VITE_API_BASE.
const API_BASE = import.meta.env.VITE_API_BASE ?? `${import.meta.env.BASE_URL}api/v1`;

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined | null>;
  skipAuth?: boolean;
  formData?: FormData;
  /**
   * Override response parsing. Default behavior parses based on content-type
   * (JSON when application/json, text otherwise). Use "blob" to receive the raw
   * binary payload (e.g. zip downloads), or "text" to force string output.
   */
  responseType?: "json" | "text" | "blob";
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(
    API_BASE + (path.startsWith("/") ? path : `/${path}`),
    window.location.origin,
  );
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== "") {
        url.searchParams.set(key, String(value));
      }
    }
  }
  return url.pathname + url.search;
}

async function refreshAccessToken(): Promise<string | null> {
  const refresh = useAuthStore.getState().refreshToken;
  if (!refresh) return null;
  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (!res.ok) {
    useAuthStore.getState().clear();
    return null;
  }
  const data = await res.json();
  useAuthStore.getState().setTokens({
    access_token: data.access_token,
    refresh_token: data.refresh_token ?? refresh,
    token_type: "bearer",
    expires_in: data.expires_in ?? 3600,
  });
  return data.access_token as string;
}

export async function apiRequest<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const { body, query, skipAuth, formData, headers, responseType, ...rest } = opts;
  const url = buildUrl(path, query);

  const baseHeaders: Record<string, string> = {
    accept: "application/json",
    ...((headers as Record<string, string>) ?? {}),
  };

  let requestBody: BodyInit | undefined;
  if (formData) {
    requestBody = formData;
  } else if (body !== undefined) {
    baseHeaders["content-type"] = "application/json";
    requestBody = JSON.stringify(body);
  }

  if (!skipAuth) {
    const token = useAuthStore.getState().accessToken;
    if (token) baseHeaders.authorization = `Bearer ${token}`;
  }

  let res = await fetch(url, { ...rest, headers: baseHeaders, body: requestBody });

  if (res.status === 401 && !skipAuth) {
    const newToken = await refreshAccessToken();
    if (newToken) {
      baseHeaders.authorization = `Bearer ${newToken}`;
      res = await fetch(url, { ...rest, headers: baseHeaders, body: requestBody });
    }
  }

  if (!res.ok) {
    let detail: unknown;
    let message = res.statusText;
    let code: string | undefined;
    try {
      const data = await res.json();
      detail = data;
      // 1) HEAXHub envelope: { error: { message, code, details } }
      // 2) FastAPI default: { detail: "..." } or { detail: [{loc, msg}, ...] }
      // 3) Other: { message: "..." }
      if (data && typeof data === "object") {
        if ("error" in data && data.error && typeof data.error === "object") {
          const err = data.error as { message?: string; code?: string };
          if (err.message) message = err.message;
          if (err.code) code = err.code;
        } else if ("detail" in data) {
          const d = (data as { detail: unknown }).detail;
          if (typeof d === "string") {
            message = d;
          } else if (Array.isArray(d) && d.length > 0) {
            // Pydantic 422 array of { loc, msg, type }
            const first = d[0] as { msg?: string; loc?: unknown[] };
            if (first?.msg) {
              const where = Array.isArray(first.loc)
                ? first.loc.filter((p) => p !== "body").join(".")
                : "";
              message = where ? `${where}: ${first.msg}` : first.msg;
            }
          }
        } else if ("message" in data && typeof (data as { message: unknown }).message === "string") {
          message = (data as { message: string }).message;
        }
      }
    } catch {
      /* not JSON */
    }
    throw new ApiError(
      typeof message === "string" ? message : "Request failed",
      res.status,
      code,
      detail,
    );
  }

  if (res.status === 204) return undefined as T;

  if (responseType === "blob") {
    return (await res.blob()) as unknown as T;
  }
  if (responseType === "text") {
    return (await res.text()) as unknown as T;
  }

  const ct = res.headers.get("content-type") ?? "";
  if (responseType === "json" || ct.includes("application/json")) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => apiRequest<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: "PATCH", body }),
  put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: "PUT", body }),
  del: <T>(path: string, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: "DELETE" }),
  upload: <T>(path: string, formData: FormData, opts?: RequestOptions) =>
    apiRequest<T>(path, { ...opts, method: "POST", formData }),
};
