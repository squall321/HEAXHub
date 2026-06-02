import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { AuthTokens, User } from "@/lib/api/types";

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: User | null;
  expiresAt: number | null;
  setSession: (user: User, tokens: AuthTokens) => void;
  setUser: (user: User | null) => void;
  setTokens: (tokens: AuthTokens) => void;
  clear: () => void;
  isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      expiresAt: null,

      setSession: (user, tokens) =>
        set({
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
          user,
          expiresAt: Date.now() + tokens.expires_in * 1000,
        }),

      setUser: (user) => set({ user }),

      setTokens: (tokens) =>
        set({
          accessToken: tokens.access_token,
          refreshToken: tokens.refresh_token,
          expiresAt: Date.now() + tokens.expires_in * 1000,
        }),

      clear: () =>
        set({ accessToken: null, refreshToken: null, user: null, expiresAt: null }),

      isAuthenticated: () => {
        const { accessToken, user } = get();
        return Boolean(accessToken && user);
      },
    }),
    {
      name: "heaxhub.auth",
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
        expiresAt: state.expiresAt,
      }),
    },
  ),
);
