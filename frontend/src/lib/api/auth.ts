import { api } from "./client";
import type { AuthTokens, LoginResponse, User } from "./types";

export interface RegisterPayload {
  display_name: string;
  organization: string;
  email: string;
  password: string;
  password_confirm: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export const authApi = {
  register: (payload: RegisterPayload) =>
    api.post<User>("/auth/register", payload, { skipAuth: true }),

  verifyEmail: (token: string) =>
    api.post<{ ok: true }>("/auth/verify-email", { token }, { skipAuth: true }),

  login: (payload: LoginPayload) =>
    api.post<LoginResponse>("/auth/login", payload, { skipAuth: true }),

  refresh: (refreshToken: string) =>
    api.post<AuthTokens>("/auth/refresh", { refresh_token: refreshToken }, { skipAuth: true }),

  logout: () => api.post<{ ok: true }>("/auth/logout"),

  me: () => api.get<User>("/auth/me"),

  passwordResetRequest: (email: string) =>
    api.post<{ ok: true }>(
      "/auth/password/reset-request",
      { email },
      { skipAuth: true },
    ),

  passwordReset: (token: string, password: string, passwordConfirm: string) =>
    api.post<{ ok: true }>(
      "/auth/password/reset",
      { token, password, password_confirm: passwordConfirm },
      { skipAuth: true },
    ),

  updateProfile: (payload: { display_name?: string; organization?: string }) =>
    api.patch<User>("/users/me", payload),

  // ── PAT (개인 액세스 토큰) — MCP 게이트웨이 연동에도 이 토큰을 그대로 쓴다 ──
  listTokens: () => api.get<PatPublic[]>("/auth/tokens"),
  createToken: (name: string, expiresDays?: number | null) =>
    api.post<PatCreated>("/auth/tokens", { name, expires_days: expiresDays ?? null }),
  revokeToken: (id: string) => api.del<void>(`/auth/tokens/${id}`),
};

export interface PatPublic {
  id: string;
  name: string;
  token_prefix: string;
  created_at: string;
  expires_at: string | null;
  last_used_at?: string | null;
}
export interface PatCreated extends PatPublic {
  token: string; // 평문 — 발급 응답에서 단 한 번만.
}
