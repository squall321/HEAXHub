import { api } from "./client";
import type { Job, JobDetail, JobStatus, Pagination } from "./types";

export interface JobListQuery {
  status?: JobStatus;
  app_id?: string;
  q?: string;
  page?: number;
  page_size?: number;
  mine?: boolean;
  [key: string]: string | number | boolean | undefined | null;
}

export const jobsApi = {
  list: (query?: JobListQuery) => api.get<Pagination<Job>>("/jobs", { query }),
  detail: (jobId: string) => api.get<JobDetail>(`/jobs/${jobId}`),
  logs: (jobId: string) => api.get<string>(`/jobs/${jobId}/logs`),
  files: (jobId: string) =>
    api.get<{ files: { name: string; path: string; size: number }[] }>(
      `/jobs/${jobId}/files`,
    ),
  fileUrl: (jobId: string, path: string) =>
    `${import.meta.env.VITE_API_BASE ?? "/api/v1"}/jobs/${jobId}/files/${encodeURIComponent(path)}`,
  cancel: (jobId: string) => api.post<{ ok: true }>(`/jobs/${jobId}/cancel`),
  rerun: (jobId: string) => api.post<{ job_id: string }>(`/jobs/${jobId}/rerun`),
};
