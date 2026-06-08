"""Application configuration (Pydantic Settings)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Single source of truth for environment-driven configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App basics ---
    # Defaults match the Apptainer runtime port set; override via .env per host.
    app_env: Literal["development", "staging", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 4040
    app_base_url: str = "http://localhost:4040"
    frontend_base_url: str = "http://localhost:4173"

    # --- DB / Redis ---
    database_url: str = "postgresql+psycopg://heaxhub:heaxhub@localhost:5732/heaxhub"
    redis_url: str = "redis://localhost:6479/0"

    # --- Auth ---
    auth_mode: Literal["local", "sso"] = "local"
    allowed_email_domains: str = "company.com,example.com"
    jwt_secret: str = "change-me-to-a-strong-random-secret"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 3600
    refresh_token_ttl_seconds: int = 604800
    # HWAXAgent launcher refresh token lives longer than a user session (30 days)
    # so a tray agent offline for a while can still refresh (contract v0.2.0).
    agent_refresh_token_ttl_seconds: int = 2592000
    password_min_length: int = 10
    email_verify_token_ttl_hours: int = 24
    password_reset_token_ttl_hours: int = 2

    # --- Mail ---
    smtp_host: str = "localhost"
    smtp_port: int = 8125
    smtp_user: str = ""
    smtp_password: str = ""
    mail_from: str = "heaxhub-noreply@example.com"
    mail_dry_run: bool = True

    # --- Storage ---
    job_storage_root: Path = Path("./job_storage")
    workspace_root: Path = Path("./app_workspaces")

    # --- Git policy ---
    allowed_git_hosts: str = "github.com,git.company.com"

    # --- Build policy ---
    python_build_path: str = "/usr/bin/python3"
    # Default tries /usr/local/bin first (Apptainer 1.3+ ships there).
    # Override via APPTAINER_BIN if your distro puts it elsewhere.
    apptainer_bin: str = "/usr/local/bin/apptainer"
    build_timeout_seconds: int = 1800

    # --- Seed admin (used by scripts/create_admin.py) ---
    seed_admin_email: str = "admin@example.com"
    seed_admin_name: str = "System Admin"
    seed_admin_org: str = "CAE Automation Part"
    seed_admin_password: str = "ChangeMe-On-First-Login!"

    # --- Webhooks ---
    github_webhook_secret: str = ""

    # --- CORS ---
    cors_origins: str = Field(default="http://localhost:4173,http://localhost:4180")

    # --- v2 common infrastructure (SA1) ---
    app_port_range_low: int = 9100
    app_port_range_high: int = 9999
    public_host: str = "localhost"
    public_port: int = 4180
    caddy_admin_url: str = "http://127.0.0.1:2019"
    secret_encryption_key: str = ""  # Fernet base64-encoded 32 bytes
    interpreters_config: Path = Path("config/interpreters.yaml")

    # --- GitHub integration (SA3 change_request publishing) ---
    github_bot_token: str = ""
    github_bot_username: str = "heaxhub-bot"
    # 시연·테스트용 단축 repo. 단일 값(하위 호환) 또는 INTEGRATION_REPO_URLS 콤마 구분 다중.
    integration_repo_url: str = ""
    integration_repo_urls: str = ""  # comma-separated; empty falls back to integration_repo_url

    @property
    def integration_repo_url_list(self) -> list[str]:
        """Resolved list of integration repos. Prefers _URLS, falls back to _URL."""
        if self.integration_repo_urls.strip():
            return [u.strip() for u in self.integration_repo_urls.split(",") if u.strip()]
        if self.integration_repo_url.strip():
            return [self.integration_repo_url.strip()]
        return []

    # --- LLM (Manifest Inferrer — SA2) ---
    llm_provider: Literal["anthropic", "openai", "local", "stub"] = "anthropic"
    llm_api_key: str = ""
    llm_model: str = "claude-sonnet-4-5"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 8000
    llm_local_endpoint: str = ""

    # --- v2 Windows Agent / installer hosting (SA5) ---
    installer_storage_root: Path = Path("./var/installers")
    windows_agent_poll_interval: int = 5

    # --- v2 apptainer + GPU + license (SA4) ---
    apptainer_default_binds: str = ""  # comma-separated host[:container[:opts]] entries
    # Logical-name -> absolute SIF path registry consumed by
    # app.services.sif_registry. Relative paths resolved against the project
    # root (where the backend process is launched).
    sif_registry_path: Path = Path("config/sif_registry.yaml")
    # Per-stack toolchain SIFs (heaxhub_toolchain_<key>.sif) used by the
    # integration builder to dispatch pip / pnpm / go / dotnet / mvn / cargo
    # inside a hermetic Apptainer image instead of the host PATH. When empty,
    # the resolver falls back to SIF_DIR (legacy operator override) and then
    # to ~/serviceApptainers/ for dev workstations.
    toolchain_sif_dir: str = Field(
        default="",
        validation_alias=AliasChoices("HEAXHUB_TOOLCHAIN_SIF_DIR", "toolchain_sif_dir"),
        description=(
            "Override directory for heaxhub_toolchain_*.sif. Empty falls back "
            "to SIF_DIR / ~/serviceApptainers."
        ),
    )

    # --- License provider (FlexLM/mock) ---
    license_provider: Literal["flexlm", "mock"] = "mock"
    flexlm_lmstat_bin: str = "/usr/local/flexlm/bin/lmstat"
    flexlm_license_file: str = ""  # exported as LM_LICENSE_FILE when invoking lmstat
    # Comma-separated "feature:count" pairs used by MockLicenseProvider.
    mock_license_features: str = "lsdyna:8,ansys:4"

    # --- Slurm runner ---
    slurm_sbatch_bin: str = "/usr/local/slurm/bin/sbatch"
    slurm_squeue_bin: str = "/usr/local/slurm/bin/squeue"
    slurm_sacct_bin: str = "/usr/local/slurm/bin/sacct"
    slurm_scancel_bin: str = "/usr/local/slurm/bin/scancel"
    slurm_default_partition: str = "normal"
    slurm_default_time_minutes: int = 60

    @field_validator("job_storage_root", "workspace_root", "installer_storage_root", mode="before")
    @classmethod
    def _expand_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser().resolve()

    @field_validator("interpreters_config", mode="before")
    @classmethod
    def _expand_interpreters_path(cls, v: str | Path) -> Path:
        return Path(v).expanduser()

    @field_validator("sif_registry_path", mode="before")
    @classmethod
    def _expand_sif_registry_path(cls, v: str | Path) -> Path:
        # Keep relative so callers can resolve against project root if needed;
        # only expand ~ so tilde paths still work from env vars.
        return Path(v).expanduser()

    @property
    def allowed_email_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]

    @property
    def allowed_git_host_list(self) -> list[str]:
        return [h.strip().lower() for h in self.allowed_git_hosts.split(",") if h.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        """Resolve CORS allow-list.

        - When ``CORS_ORIGINS`` is empty, default to ``frontend_base_url``.
        - ``*`` is only honored when ``CORS_ORIGINS`` explicitly contains it,
          and is then returned as the sole entry (browsers reject ``*`` mixed
          with credentials, but the explicit opt-in is intentional).
        """
        raw = (self.cors_origins or "").strip()
        if not raw:
            return [self.frontend_base_url] if self.frontend_base_url else []
        parts = [o.strip() for o in raw.split(",") if o.strip()]
        if "*" in parts:
            return ["*"]
        return parts

    @property
    def apptainer_default_bind_list(self) -> list[str]:
        return [b.strip() for b in self.apptainer_default_binds.split(",") if b.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor; safe for FastAPI dependency injection."""
    return Settings()
