import { api } from "./client";
import type {
  InstallerPackage,
  WindowsAgent,
  WindowsAgentCreatePayload,
  WindowsAgentIssueResponse,
} from "./types";

export const agentsApi = {
  list: () => api.get<WindowsAgent[]>("/admin/agents"),
  detail: (id: string) => api.get<WindowsAgent>(`/admin/agents/${id}`),
  // Returns { agent, token } — `token` is the one-time plaintext enrollment
  // token, NEVER shown again after this response.
  create: (payload: WindowsAgentCreatePayload) =>
    api.post<WindowsAgentIssueResponse>("/admin/agents", payload),
  remove: (id: string) => api.del<{ ok: true }>(`/admin/agents/${id}`),
  // Rotate enrollment token (also returned only once).
  rotateToken: (id: string) =>
    api.post<WindowsAgentIssueResponse>(`/admin/agents/${id}/rotate-token`),
};

export const installersApi = {
  list: (appId: string) =>
    api.get<InstallerPackage[]>(`/apps/${appId}/installers`),
  upload: (appId: string, fd: FormData) =>
    api.upload<InstallerPackage>(`/apps/${appId}/installers`, fd),
  remove: (appId: string, installerId: string) =>
    api.del<{ ok: true }>(`/apps/${appId}/installers/${installerId}`),
};
