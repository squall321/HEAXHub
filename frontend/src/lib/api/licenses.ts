import { api } from "./client";
import type {
  LicenseHolding,
  LicensePool,
  LicensePoolCreatePayload,
  LicenseUsagePoint,
} from "./types";

export const licensesApi = {
  list: () => api.get<LicensePool[]>("/admin/licenses"),
  detail: (name: string) => api.get<LicensePool>(`/admin/licenses/${name}`),
  create: (payload: LicensePoolCreatePayload) =>
    api.post<LicensePool>("/admin/licenses", payload),
  update: (name: string, payload: Partial<LicensePoolCreatePayload>) =>
    api.patch<LicensePool>(`/admin/licenses/${name}`, payload),
  remove: (name: string) => api.del<{ ok: true }>(`/admin/licenses/${name}`),
  holdings: (name: string) =>
    api.get<LicenseHolding[]>(`/admin/licenses/${name}/holdings`),
  usage: (name: string, query?: { hours?: number }) =>
    api.get<LicenseUsagePoint[]>(`/admin/licenses/${name}/usage`, { query }),
};
