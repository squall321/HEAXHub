import { api } from "./client";
import type { ServiceInstance } from "./types";

export const servicesApi = {
  list: (query?: { app_id?: string; status?: string }) =>
    api.get<ServiceInstance[]>("/admin/services", { query }),
  detail: (id: string) => api.get<ServiceInstance>(`/admin/services/${id}`),
  restart: (id: string) =>
    api.post<{ ok: true; service: ServiceInstance }>(`/admin/services/${id}/restart`),
  stop: (id: string) =>
    api.post<{ ok: true; service: ServiceInstance }>(`/admin/services/${id}/stop`),
  logs: (id: string, query?: { tail?: number }) =>
    api.get<{ lines: string[] }>(`/admin/services/${id}/logs`, { query }),
};
