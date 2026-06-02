import { api } from "./client";
import type { Pagination, Secret, SecretCreatePayload, SecretUpdatePayload } from "./types";

export interface SecretListQuery {
  scope?: "global" | "app" | "job";
  scope_ref?: string;
  q?: string;
  page?: number;
  page_size?: number;
  [key: string]: string | number | boolean | undefined | null;
}

export const secretsApi = {
  list: (query?: SecretListQuery) =>
    api.get<Pagination<Secret>>("/admin/secrets", { query }),
  detail: (id: string) => api.get<Secret>(`/admin/secrets/${id}`),
  create: (payload: SecretCreatePayload) =>
    api.post<Secret>("/admin/secrets", payload),
  update: (id: string, payload: SecretUpdatePayload) =>
    api.patch<Secret>(`/admin/secrets/${id}`, payload),
  remove: (id: string) => api.del<{ ok: true }>(`/admin/secrets/${id}`),
};
