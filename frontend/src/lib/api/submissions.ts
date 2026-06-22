import { api } from "./client";
import type { Manifest, Pagination, SourceConfig, Submission } from "./types";

export interface SubmissionCreatePayload {
  proposed_app_id: string;
  name: string;
  description?: string;
  upstream_repo_url: string;
  app_type: string;
  execution_target: string;
  proposed_manifest?: Manifest;
  // v2: optional source descriptor for non-git sources
  source_config?: SourceConfig;
}

export const submissionsApi = {
  list: (query?: { mine?: boolean; status?: string; page?: number; page_size?: number }) =>
    api.get<Pagination<Submission>>("/submissions", { query }),
  detail: (id: string) => api.get<Submission>(`/submissions/${id}`),
  create: (payload: SubmissionCreatePayload) => api.post<Submission>("/submissions", payload),
  approve: (id: string, notes?: string) =>
    api.patch<Submission>(`/submissions/${id}`, { status: "approved", review_notes: notes }),
  reject: (id: string, notes?: string) =>
    api.patch<Submission>(`/submissions/${id}`, { status: "rejected", review_notes: notes }),
  testRun: (id: string) => api.post<{ job_id: string }>(`/submissions/${id}/test-run`),
  publish: (id: string) => api.post<Submission>(`/submissions/${id}/publish`),
  retry: (id: string) => api.post<Submission>(`/submissions/${id}/retry`),
  buildLog: (id: string, tail?: number) =>
    api.get<string>(`/submissions/${id}/build-log`, {
      query: { tail },
      responseType: "text",
    }),
};
