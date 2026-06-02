import { api } from "./client";
import type { GpuDevice, GpuHolding } from "./types";

export const gpusApi = {
  list: () => api.get<GpuDevice[]>("/admin/gpus"),
  holdings: () => api.get<GpuHolding[]>("/admin/gpus/holdings"),
  refresh: () =>
    api.post<{ ok: true; devices: GpuDevice[] }>("/admin/gpus/refresh"),
  update: (id: string, payload: { status?: "available" | "in_use" | "offline" }) =>
    api.patch<GpuDevice>(`/admin/gpus/${id}`, payload),
};
