import { api } from "./client";
import type { ChangeRequest, IntegrationConfig } from "./types";

export const integrationsApi = {
  config: () => api.get<IntegrationConfig>("/admin/integrations"),
  update: (payload: Partial<IntegrationConfig> & { github_bot_token?: string }) =>
    api.patch<IntegrationConfig>("/admin/integrations", payload),
  // Emits a new ChangeRequest against INTEGRATION_REPO_URL for end-to-end testing.
  testRequest: () =>
    api.post<{ change_request: ChangeRequest }>("/admin/integrations/test-request"),
};
