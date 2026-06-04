/**
 * Hand-rolled TypeScript types mirroring backend Pydantic schemas.
 * Keep in sync with backend/app/schemas/*.
 */

export type AuthSource = "local" | "sso";
export type UserStatus = "pending_verification" | "active" | "disabled";
export type UserRole = "admin" | "owner" | "user" | "viewer";

export interface User {
  id: string;
  email: string;
  display_name: string;
  organization: string;
  auth_source: AuthSource;
  status: UserStatus;
  role: UserRole;
  email_verified: boolean;
  last_login_at?: string | null;
  created_at: string;
}

export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface LoginResponse extends AuthTokens {
  user: User;
}

export type AppType =
  | "cli_tool"
  | "web_app"
  | "windows_gui"
  | "remote_app"
  | "external_link"
  | "slurm_job"
  | "container_app";

export type ExecutionTarget =
  | "linux_runner"
  | "slurm"
  | "apptainer"
  | "windows_worker"
  | "external_url"
  | "local_pc";

export type AppStatus = "draft" | "beta" | "stable" | "deprecated" | "archived";
export type Visibility = "private" | "team" | "department" | "company";

export interface AppSummary {
  id: string;
  name: string;
  description?: string | null;
  owner_user_id: string;
  owner_display?: string | null;
  current_version?: string | null;
  app_type: AppType;
  execution_target: ExecutionTarget;
  status: AppStatus;
  visibility: Visibility;
  tags: string[];
  updated_at: string;
}

export interface ManifestInput {
  name: string;
  type: "file" | "folder" | "string" | "number" | "integer" | "boolean" | "enum";
  label?: string;
  description?: string;
  required?: boolean;
  default?: unknown;
  extensions?: string[];
  options?: (string | number)[];
  min?: number;
  max?: number;
}

export interface ManifestOutput {
  name: string;
  type?: "file" | "folder";
  path: string;
  description?: string;
}

export interface Manifest {
  schema_version: 1;
  id: string;
  name: string;
  version: string;
  owner: string;
  status: AppStatus;
  app_type: AppType;
  execution_target: ExecutionTarget;
  description?: string;
  tags?: string[];
  launch: {
    mode: "job_runner" | "url" | "remote_agent" | "local_protocol";
    command?: string;
    url?: string;
    open_in?: "new_tab" | "iframe";
    auth_mode?: "none" | "sso" | "token";
    runtime?: "python_venv" | "apptainer" | "shell" | "windows_exe";
    [k: string]: unknown;
  };
  inputs?: ManifestInput[];
  outputs?: ManifestOutput[];
  permissions?: {
    visibility?: Visibility;
    executable_by?: string[];
  };
  resources?: {
    cpu?: number;
    memory_gb?: number;
    gpu?: boolean;
    timeout_seconds?: number;
  };
  build?: {
    type: "python_venv" | "apptainer" | "none" | "external";
    python_version?: string;
    requirements_file?: string;
    [k: string]: unknown;
  };
}

export interface AppVersion {
  id: string;
  app_id: string;
  version: string;
  git_commit_hash: string | null;
  git_tag?: string | null;
  build_status: "pending" | "building" | "success" | "failed";
  released_at: string | null;
  released_by?: string | null;
}

export interface AppDetail extends AppSummary {
  upstream_repo_url: string;
  overlay_repo_url?: string | null;
  workspace_path: string;
  manifest?: Manifest | null;
  versions: AppVersion[];
  readme?: string | null;
  changelog?: string | null;
  created_at: string;
}

export type JobStatus = "queued" | "running" | "success" | "failed" | "canceled";

export interface Job {
  id: string;
  app_id: string;
  app_name?: string | null;
  app_version_id: string;
  executor_user_id: string;
  executor_display?: string | null;
  status: JobStatus;
  execution_target: ExecutionTarget;
  started_at?: string | null;
  finished_at?: string | null;
  duration_sec?: number | null;
  created_at: string;
}

export interface JobDetail extends Job {
  params_json: Record<string, unknown>;
  input_files: string[];
  storage_path: string;
  result_summary?: Record<string, unknown> | null;
  output_files?: { name: string; path: string; size: number }[];
}

export type SubmissionStatus =
  | "pending"
  | "under_review"
  | "approved"
  | "rejected"
  | "built"
  | "published"
  | "manifest_required";

export interface Submission {
  id: string;
  submitter_user_id: string;
  submitter_display?: string | null;
  proposed_app_id: string;
  name: string;
  description?: string | null;
  upstream_repo_url: string;
  proposed_manifest?: Manifest | null;
  status: SubmissionStatus;
  review_notes?: string | null;
  reviewer_user_id?: string | null;
  created_at: string;
  reviewed_at?: string | null;
  published_at?: string | null;
}

export interface UpdateProposal {
  id: string;
  app_id: string;
  app_name: string;
  current_commit: string;
  latest_commit: string;
  latest_tag?: string | null;
  detected_at: string;
  status: "pending" | "approved" | "ignored";
}

export interface AuditEntry {
  id: number;
  actor_user_id?: string | null;
  actor_display?: string | null;
  action: string;
  target_type: string;
  target_id: string;
  meta?: Record<string, unknown>;
  ip_address?: string | null;
  created_at: string;
}

export interface Pagination<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// v2 additions (SA6 — frontend gaps)
// Mirrors backend SA1~SA5 work (Alembic 0003 + new endpoints).
// ---------------------------------------------------------------------------

export type SecretScope = "global" | "app" | "job";

export interface Secret {
  id: string;
  key: string;
  scope: SecretScope;
  scope_ref?: string | null;
  description?: string | null;
  created_at: string;
  updated_at?: string | null;
  // value is NEVER returned by the API after creation
}

export interface SecretCreatePayload {
  key: string;
  value: string;
  scope: SecretScope;
  scope_ref?: string | null;
  description?: string | null;
}

export interface SecretUpdatePayload {
  value?: string;
  description?: string | null;
}

export interface LicensePool {
  id: string;
  name: string;
  vendor?: string | null;
  feature?: string | null;
  total_tokens: number;
  in_use_tokens: number;
  description?: string | null;
  blocking: boolean;
  created_at: string;
}

export interface LicensePoolCreatePayload {
  name: string;
  vendor?: string;
  feature?: string;
  total_tokens: number;
  description?: string;
  blocking?: boolean;
}

export interface LicenseHolding {
  id: string;
  pool_name: string;
  job_id: string;
  job_app_id?: string | null;
  tokens: number;
  acquired_at: string;
  released_at?: string | null;
}

export interface LicenseUsagePoint {
  timestamp: string;
  in_use: number;
  total: number;
}

export interface GpuDevice {
  id: string;
  index: number;
  uuid?: string | null;
  model: string;
  memory_mb: number;
  cuda_version?: string | null;
  status: "available" | "in_use" | "offline";
  host?: string | null;
  current_job_id?: string | null;
  updated_at: string;
}

export interface GpuHolding {
  id: string;
  gpu_id: string;
  job_id: string;
  acquired_at: string;
  released_at?: string | null;
}

export type ServiceStatus =
  | "starting"
  | "healthy"
  | "unhealthy"
  | "stopped";

export interface ServiceInstance {
  id: string;
  app_id: string;
  app_name?: string | null;
  app_version_id?: string | null;
  port?: number | null;
  base_path?: string | null;
  pid?: number | null;
  status: ServiceStatus;
  health_url?: string | null;
  last_health_at?: string | null;
  restart_count: number;
  started_at: string;
  stopped_at?: string | null;
}

export interface WindowsAgent {
  id: string;
  hostname: string;
  os_version?: string | null;
  arch?: string | null;
  status: "online" | "offline" | "disabled";
  last_seen_at?: string | null;
  version?: string | null;
  capabilities?: string[];
  created_at: string;
}

export interface WindowsAgentCreatePayload {
  hostname: string;
  description?: string;
  capabilities?: string[];
}

export interface WindowsAgentIssueResponse extends WindowsAgent {
  // One-time token, only returned on creation
  enrollment_token: string;
}

export interface InstallerPackage {
  id: string;
  app_id: string;
  os: string;
  version: string;
  filename: string;
  size_bytes: number;
  sha256: string;
  download_url: string;
  uploaded_at: string;
  uploaded_by?: string | null;
}

export interface IntegrationConfig {
  integration_repo_url?: string | null;
  github_bot_username?: string | null;
  token_configured: boolean;
  webhook_configured: boolean;
  llm_provider?: string | null;
  llm_model?: string | null;
}

export type ChangeRequestStatus =
  | "draft"
  | "awaiting_assistant"
  | "assistant_responded"
  | "issued_md"
  | "issued_pr"
  | "issued_issue"
  | "merged"
  | "rejected"
  | "superseded";

export interface OpenQuestion {
  field: string;
  question: string;
  candidates?: (string | number)[];
  context?: string;
}

export interface RequiredFile {
  path: string;
  kind: "create" | "append" | "modify";
  content: string;
  mode?: string;
}

export interface DeveloperChangeRequest {
  summary: string;
  required_files: RequiredFile[];
  suggested_files?: RequiredFile[];
  rationale?: string;
}

export interface LLMResult {
  manifest_draft: Record<string, unknown>;
  confidence: Record<string, number>;
  open_questions: OpenQuestion[];
  developer_change_request: DeveloperChangeRequest;
}

export interface ChangeRequest {
  id: string;
  submission_id?: string | null;
  app_id?: string | null;
  repo_url: string;
  commit_sha?: string | null;
  static_facts: Record<string, unknown>;
  llm_response: LLMResult;
  operator_overrides: Record<string, unknown>;
  final_manifest: Record<string, unknown>;
  markdown_body: string;
  pr_payload?: Record<string, unknown> | null;
  status: ChangeRequestStatus;
  pr_url?: string | null;
  issue_url?: string | null;
  issued_at?: string | null;
  merged_at?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at?: string | null;
  // Claude-in-the-loop assistant handoff
  assistant_packet_available?: boolean;
  assistant_responded_at?: string | null;
}

export interface ChangeRequestCreatePayload {
  submission_id?: string | null;
  repo_url: string;
  // optional pre-known source descriptor (for non-git sources)
  source_type?: "git" | "archive_url" | "local_path" | "system_command";
  source_config?: Record<string, unknown>;
}

export interface ChangeRequestUpdatePayload {
  operator_overrides?: Record<string, unknown>;
}

export type IssueVia = "pr" | "issue" | "markdown";

export interface IssueResponse {
  ok: boolean;
  pr_url?: string;
  issue_url?: string;
  status: ChangeRequestStatus;
}

// Submission v2 — source abstraction
export type SourceType = "git" | "archive_url" | "local_path" | "system_command";

export interface SourceConfig {
  type: SourceType;
  url?: string;
  sha256?: string;
  path?: string;
  verify_command?: string;
}

export class ApiError extends Error {
  status: number;
  code?: string;
  details?: unknown;
  constructor(message: string, status: number, code?: string, details?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}
