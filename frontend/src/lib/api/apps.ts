import { api } from "./client";
import type {
  AppDetail,
  AppSummary,
  AppType,
  AppVersion,
  ExecutionTarget,
  Job,
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
  /** HEAXHub stack key (e.g. "fastapi", "streamlit"); matches manifest.build.stack. */
  stack?: string;
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
  run: (appId: string, body: { params: Record<string, unknown>; files?: File[] }) => {
    const fd = new FormData();
    fd.append("params_json", JSON.stringify(body.params ?? {}));
    for (const f of body.files ?? []) {
      fd.append("files", f, f.name);
    }
    return api.upload<Job>(`/apps/${appId}/run`, fd);
  },
};
