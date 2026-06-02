import { api } from "./client";
import type {
  AppDetail,
  AppSummary,
  AppType,
  AppVersion,
  ExecutionTarget,
  Manifest,
  Pagination,
  Visibility,
} from "./types";

export interface AppListQuery {
  q?: string;
  app_type?: AppType;
  execution_target?: ExecutionTarget;
  visibility?: Visibility;
  status?: string;
  tag?: string;
  page?: number;
  page_size?: number;
  sort?: "updated_at" | "name" | "popularity";
  [key: string]: string | number | boolean | undefined | null;
}

export const appsApi = {
  list: (query?: AppListQuery) => api.get<Pagination<AppSummary>>("/apps", { query }),
  recommended: () => api.get<AppSummary[]>("/apps/recommended"),
  favorites: () => api.get<AppSummary[]>("/apps/favorites"),
  toggleFavorite: (appId: string) =>
    api.post<{ favorited: boolean }>(`/apps/${appId}/favorite`),
  detail: (appId: string) => api.get<AppDetail>(`/apps/${appId}`),
  manifest: (appId: string) => api.get<Manifest>(`/apps/${appId}/manifest`),
  versions: (appId: string) => api.get<AppVersion[]>(`/apps/${appId}/versions`),
  history: (appId: string) =>
    api.get<{ items: { id: string; status: string; created_at: string; user: string }[] }>(
      `/apps/${appId}/history`,
    ),
  run: (appId: string, body: { params: Record<string, unknown>; files?: FormData }) => {
    if (body.files) {
      const fd = body.files;
      fd.append("params", JSON.stringify(body.params));
      return api.upload<{ job_id: string }>(`/apps/${appId}/run`, fd);
    }
    return api.post<{ job_id: string }>(`/apps/${appId}/run`, { params: body.params });
  },
};
