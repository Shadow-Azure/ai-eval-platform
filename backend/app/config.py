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
    # Aliyun OSS requires virtual-hosted style (bucket.oss-cn-*.aliyuncs.com);
    # MinIO/local uses path style. auto picks OSS for *.aliyuncs.com.
    s3_addressing_style: str = "auto"

    default_tenant_slug: str = "default"
    default_workspace_slug: str = "default"
    default_project_slug: str = "default"

    # M3-05 console identity. The session cookie carries an HMAC-signed opaque
    # token; only its SHA-256 hash is stored. Production must configure
    # AUTH_SESSION_SECRET (the deploy script generates one on first rollout).
    auth_session_secret: str | None = None
    auth_session_ttl_hours: int = 12
    auth_session_remember_days: int = 30
    auth_cookie_name: str = "eval_session"
    # Plain-HTTP deployments must keep this false; set true behind TLS.
    auth_cookie_secure: bool = False
    # First administrator bootstrap. The password is required for initial
    # provisioning; seed never overwrites an existing account's password.
    auth_bootstrap_admin_email: str = "admin@acme.local"
    auth_bootstrap_admin_password: str | None = None

    worker_poll_seconds: float = 5.0
    worker_heartbeat_path: str = "/tmp/eval-worker-heartbeat"

    # M3-06 operations status: provisioned database storage capacity, used to
    # show a usage watermark on the admin page. 0 means "not configured"; the
    # page then reports the current size without a percentage.
    database_storage_limit_bytes: int = 0

    # M2-04 fixed runner plane. The heartbeat token matches the env key the
    # deployed runner agent reads (/etc/eval-platform/runner.env); when unset
    # the runner plane accepts unauthenticated requests (development only).
    runner_heartbeat_token: str | None = None
    runner_heartbeat_timeout_seconds: int = 90
    runner_fixed_pool_name: str = "fixed-linux"
    runner_fixed_pool_provider_key: str = "fixed"
    # M4-04 ephemeral spot runners. RUNNER_ADMIN_TOKEN is a repository-admin
    # PAT/App token used only for generate-jitconfig + runner deletion; the
    # dispatch token (GITHUB_TOKEN) is intentionally not reused.
    runner_admin_token: str | None = None
    runner_spot_labels: str = "self-hosted,linux,eval"
    runner_spot_runner_version: str = "2.329.0"
    runner_spot_runner_group_id: int | None = 1

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
    # Cap synthetic case keys (and dataset cases) per evaluation creation call.
    # M4-01 acceptance needs 500 cases → 8 shards of 64, so the old 256 cap
    # could not reach the case-level matrix contract.
    eval_max_cases: int = 1000
    # M4-01 concurrency. The requested cap is the per-evaluation hard limit;
    # effective concurrency = min(requested, online pool runners, pool max).
    eval_default_requested_concurrency: int = 8
    eval_shard_size_default: int = 64
    # Capacity gate: keep shards queued when the evaluation has no effective
    # capacity instead of letting the GitHub queue be the only throttle.
    dispatch_capacity_gate: bool = True
    dispatch_capacity_retry_seconds: float = 10.0
    # M4-03 spot capacity controller: how often a pool is re-evaluated and how
    # many instances one tick may create (a bounded batch keeps one worker pass
    # cheap even when a large queue arrives at once).
    eval_capacity_check_seconds: int = 300
    eval_capacity_batch_size: int = 5
    # M4-04 Aliyun ECS spot provisioner. Aliyun quotes CNY/hour; scheduling
    # compares USD against ``price_threshold_usd`` using this rate. Credentials
    # come from the encrypted resource connection, never from these settings.
    aliyun_ecs_endpoint: str = "https://ecs.aliyuncs.com"
    aliyun_default_region: str = "cn-hangzhou"
    aliyun_cny_per_usd: float = 7.2
    aliyun_default_instance_type: str = "ecs.g7.large"
    # A spot instance is reclaimed when it never starts or never registers a
    # runner; queued attempts stay queued and the next tick retries.
    eval_spot_register_timeout_seconds: int = 900
    # Grace after the runner heartbeat timeout before an instance whose runner
    # vanished is reclaimed (the lifecycle reaper retries the attempt first).
    eval_spot_runner_lost_grace_seconds: int = 300
    # M4-02 execution lifecycle timeouts (design 06 §7). Five independent
    # clocks instead of one total timeout: platform queue, agent execution,
    # scoring, upload grace period and heartbeat. The agent execution timeout
    # is per run (``run.case_timeout_seconds``); the value here is only the
    # fallback for a run without one.
    eval_queue_timeout_seconds: int = 3600
    eval_execution_timeout_seconds: int = 900
    eval_scoring_timeout_seconds: int = 900
    eval_upload_grace_seconds: int = 300
    eval_heartbeat_timeout_seconds: int = 300
    # Attempts per lifecycle sweep; keeps one worker pass bounded.
    eval_lifecycle_batch_size: int = 100
    # Cancellation grace period (M4-02 A2). Running/scoring/uploading attempts
    # stay alive so their artifacts can settle; after this window the reconciler
    # force-cancels whatever is left so a cancelled run can never hang forever.
    eval_cancel_grace_seconds: int = 600
    # Budget pause grace period (M4-05, design 06 §8). Interrupted attempts may
    # upload their existing artifacts; after this window the reconciler settles
    # whatever is left as ``budget_paused`` so the run can reach paused_budget.
    eval_budget_pause_grace_seconds: int = 600

    # M3-02 centralized model gateway. Credentials are encrypted with a
    # host-local Fernet key (SECRET_BOX_KEY); the gateway signs short-lived
    # runner tokens with GATEWAY_TOKEN_SECRET. Both are required in
    # production (the deploy script generates them on first rollout).
    secret_box_key: str = ""
    gateway_token_secret: str | None = None
    gateway_token_ttl_seconds: int = 900
    gateway_timeout_seconds: float = 120.0
    # Connection-level retries for provider calls (bounded; never retries a
    # stream that has already started).
    gateway_connect_retries: int = 2
    # M3-04 execution/scoring image digests exposed to the launch wizard.
    default_execution_image_digest: str | None = None
    default_scoring_image_digest: str | None = None

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:8080"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def gateway_secret(self) -> str:
        """Signing secret for short-lived model gateway tokens."""

        if self.gateway_token_secret:
            return self.gateway_token_secret
        if self.environment.lower() in {"production", "prod"}:
            raise RuntimeError(
                "GATEWAY_TOKEN_SECRET is required in production; refusing to start "
                "with the development fallback"
            )
        return "development-only-gateway-token-secret"

    @property
    def session_secret(self) -> str:
        """Signing secret for session cookies.

        Production deployments must provide AUTH_SESSION_SECRET; the deploy
        script generates and persists one on first rollout. Local development
        and tests fall back to a fixed non-secret value.
        """

        if self.auth_session_secret:
            return self.auth_session_secret
        if self.environment.lower() in {"production", "prod"}:
            raise RuntimeError(
                "AUTH_SESSION_SECRET is required in production; refusing to start with "
                "the development fallback"
            )
        return "development-only-auth-session-secret"


@lru_cache
def get_settings() -> Settings:
    return Settings()
