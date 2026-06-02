import { api } from "./client";
import type { AuditEntry, Pagination, UpdateProposal, User, UserRole } from "./types";

export const adminApi = {
  systemHealth: () =>
    api.get<{
      status: string;
      queue_depth: number;
      active_jobs: number;
      db_ok: boolean;
      redis_ok: boolean;
    }>("/admin/system/health"),

  stats: () =>
    api.get<{
      jobs_today: number;
      active_users_today: number;
      build_queue_depth: number;
      pending_submissions: number;
    }>("/admin/stats"),

  users: (query?: { q?: string; role?: UserRole; page?: number; page_size?: number }) =>
    api.get<Pagination<User>>("/admin/users", { query }),

  updateUserRole: (userId: string, role: UserRole) =>
    api.patch<User>(`/admin/users/${userId}/role`, { role }),

  updates: () => api.get<UpdateProposal[]>("/admin/updates"),
  approveUpdate: (id: string) => api.post<{ ok: true }>(`/admin/updates/${id}/approve`),
  ignoreUpdate: (id: string) => api.post<{ ok: true }>(`/admin/updates/${id}/ignore`),

  audit: (query?: {
    actor?: string;
    action?: string;
    target_type?: string;
    page?: number;
    page_size?: number;
  }) => api.get<Pagination<AuditEntry>>("/admin/audit", { query }),
};
