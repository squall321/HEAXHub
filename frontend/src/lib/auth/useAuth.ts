import { useCallback } from "react";
import { authApi, type LoginPayload, type RegisterPayload } from "@/lib/api/auth";
import type { UserRole } from "@/lib/api/types";
import { useAuthStore } from "./store";

export function useAuth() {
  const user = useAuthStore((s) => s.user);
  const accessToken = useAuthStore((s) => s.accessToken);
  const setSession = useAuthStore((s) => s.setSession);
  const setUser = useAuthStore((s) => s.setUser);
  const clear = useAuthStore((s) => s.clear);

  const login = useCallback(
    async (payload: LoginPayload) => {
      const res = await authApi.login(payload);
      setSession(res.user, {
        access_token: res.access_token,
        refresh_token: res.refresh_token,
        token_type: "bearer",
        expires_in: res.expires_in,
      });
      return res.user;
    },
    [setSession],
  );

  const register = useCallback(async (payload: RegisterPayload) => {
    return authApi.register(payload);
  }, []);

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      /* ignore */
    } finally {
      clear();
    }
  }, [clear]);

  const refreshMe = useCallback(async () => {
    try {
      const me = await authApi.me();
      setUser(me);
      return me;
    } catch {
      clear();
      return null;
    }
  }, [setUser, clear]);

  const hasRole = useCallback(
    (...roles: UserRole[]) => Boolean(user && roles.includes(user.role)),
    [user],
  );

  return {
    user,
    isLoggedIn: Boolean(accessToken && user),
    login,
    register,
    logout,
    refreshMe,
    hasRole,
  };
}
