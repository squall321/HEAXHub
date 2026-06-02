import { api } from "./client";
import type {
  ChangeRequest,
  ChangeRequestCreatePayload,
  ChangeRequestStatus,
  ChangeRequestUpdatePayload,
  IssueResponse,
  IssueVia,
  Pagination,
} from "./types";

export interface ChangeRequestListQuery {
  status?: ChangeRequestStatus;
  submission_id?: string;
  app_id?: string;
  page?: number;
  page_size?: number;
  [key: string]: string | number | boolean | undefined | null;
}

export const changeRequestsApi = {
  list: (query?: ChangeRequestListQuery) =>
    api.get<Pagination<ChangeRequest>>("/change-requests", { query }),
  detail: (id: string) => api.get<ChangeRequest>(`/change-requests/${id}`),
  create: (payload: ChangeRequestCreatePayload) =>
    api.post<ChangeRequest>("/change-requests", payload),
  update: (id: string, payload: ChangeRequestUpdatePayload) =>
    api.patch<ChangeRequest>(`/change-requests/${id}`, payload),
  issue: (id: string, via: IssueVia) =>
    api.post<IssueResponse>(`/change-requests/${id}/issue`, undefined, {
      query: { via },
    }),
  // Returns text/markdown — apiRequest passes non-JSON content through as string.
  markdown: (id: string) =>
    api.get<string>(`/change-requests/${id}/markdown`),
  remove: (id: string) => api.del<{ ok: true }>(`/change-requests/${id}`),

  // ---------------------------------------------------------------------------
  // Claude-in-the-loop assistant handoff
  // ---------------------------------------------------------------------------
  /** Downloads the .zip analysis packet as a Blob. */
  downloadPacket: (id: string, opts?: { force?: "zip" | "markdown" | "md" }) =>
    api.post<Blob>(`/change-requests/${id}/assistant/packet`, undefined, {
      responseType: "blob",
      query: opts?.force ? { force: opts.force } : undefined,
    }),
  /** Returns instructions.md as plain text. */
  getInstructions: (id: string) =>
    api.get<string>(`/change-requests/${id}/assistant/instructions`, {
      responseType: "text",
    }),
  /** Submits the operator-pasted raw Claude response for validation + storage. */
  submitAssistantResponse: (id: string, rawText: string) =>
    api.post<ChangeRequest>(`/change-requests/${id}/assistant/submit`, {
      raw_text: rawText,
    }),
};
