from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Eval Platform"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://eval:eval@localhost:5432/eval"
    redis_url: str | None = None
    s3_endpoint: str | None = None
    s3_bucket: str = "eval-artifacts"
    # M2-05: OSS/MinIO credentials for presigned artifact uploads. The endpoint
    # is S3-compatible (Aliyun OSS in production, MinIO locally).
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_region: str = "us-east-1"

    default_tenant_slug: str = "default"
    default_workspace_slug: str = "default"
    default_project_slug: str = "default"

    worker_poll_seconds: float = 5.0
    worker_heartbeat_path: str = "/tmp/eval-worker-heartbeat"

    dataset_source_root: str = "/tmp/eval-datasets"
    dataset_import_max_cases: int = 5000
    dataset_import_max_bytes: int = 50 * 1024 * 1024
    object_store_root: str = "/tmp/eval-objects"

    secret_box_key: str = ""
    gateway_token_secret: str = "dev-gateway-token-secret"
    gateway_timeout_seconds: float = 120.0

    # M2-04 fixed runner plane. The heartbeat token matches the env key the
    # deployed runner agent reads (/etc/eval-platform/runner.env); when unset
    # the runner plane accepts unauthenticated requests (development only).
    runner_heartbeat_token: str | None = None
    runner_heartbeat_timeout_seconds: int = 90
    runner_fixed_pool_name: str = "fixed-linux"
    runner_fixed_pool_provider_key: str = "fixed"

    # M2-05 synthetic evaluation loop.
    # Short-term case-token secret (platform signs, runner verifies). When
    # unset the case endpoints are open (development only).
    runner_task_secret: str | None = None
    # Base URL the runner reaches for callbacks/manifests. The fixed runner
    # shares the API host, so loopback is the default.
    runner_callback_base_url: str = "http://127.0.0.1:8000"
    # Shard dispatch fan-out and reconciliation.
    dispatch_batch_size: int = 8
    dispatch_max_attempts: int = 5
    dispatch_backoff_seconds: float = 30.0
    dispatch_backoff_cap_seconds: float = 900.0
    dispatch_lease_seconds: float = 120.0
    # GitHub repository_dispatch target. GITHUB_TOKEN needs repo scope.
    github_api_url: str = "https://api.github.com"
    github_repo: str = "Shadow-Azure/ai-eval-platform"
    github_token: str | None = None
    github_dispatch_event_type: str = "eval-shard"
    # Synthetic shard manifest lifetime (dispatch expiry + case deadline).
    eval_shard_ttl_seconds: int = 3600
    eval_shard_deadline_minutes: int = 30
    # Cap synthetic case keys per evaluation creation call.
    eval_max_cases: int = 256

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:8080"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
