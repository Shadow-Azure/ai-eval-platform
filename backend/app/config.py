from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
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
    # M8 (#273): endpoint used for presigned URLs handed to spot runners. Spot
    # instances live in the same region as the bucket, so the Aliyun internal
    # endpoint is free and fast. Empty derives ``-internal`` from s3_endpoint
    # for Aliyun OSS and otherwise keeps the public endpoint.
    s3_internal_endpoint: str | None = None
    # Dataset image tarballs (issue #273): compressed ``docker save`` objects
    # are published under this prefix and presigned per case when present.
    # M8-54 (#371): the URL is signed after capacity admission, so this TTL
    # covers one download only (15 min curl budget + 5 min margin) and must
    # not be raised to cover queueing.
    dataset_image_tarball_prefix: str = "dataset-images/"
    dataset_image_tarball_ttl_seconds: int = 1200

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

    # M6-02 trace ingestion: one worker pass claims at most this many pending
    # files, and a claimed file is reclaimable after the lease expires. The
    # lease is renewed between download and parse stages.
    trace_ingestion_batch_size: int = 10
    trace_ingestion_lease_seconds: float = 300.0

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
    # M8-48 (#313): delete long-offline runner rows that no longer anchor a
    # live attempt or a non-terminal instance. Retention is deliberately much
    # longer than the heartbeat timeout so recent diagnostics stay queryable
    # while the 300+ historical zero-resident spot rows stop accumulating.
    # The worker runs the pass at most once per interval. Override with
    # EVAL_RUNNER_OFFLINE_RETENTION_SECONDS / EVAL_RUNNER_CLEANUP_BATCH_SIZE /
    # EVAL_RUNNER_CLEANUP_INTERVAL_SECONDS.
    eval_runner_offline_retention_seconds: int = 604800
    eval_runner_cleanup_batch_size: int = 200
    eval_runner_cleanup_interval_seconds: int = 3600
    # M4-04 ephemeral spot runners. RUNNER_ADMIN_TOKEN is a repository-admin
    # PAT/App token used only for generate-jitconfig + runner deletion; the
    # dispatch token (GITHUB_TOKEN) is intentionally not reused.
    runner_admin_token: str | None = None
    runner_spot_labels: str = "self-hosted,linux,eval"
    runner_spot_runner_version: str = "2.338.2"
    # Optional cached tarball URL (OSS/S3) for Aliyun regions where GitHub
    # downloads are slow/blocked; cloud-init falls back to the GitHub URL.
    runner_spot_runner_tarball_url: str | None = None
    runner_spot_runner_group_id: int | None = 1

    # M5-12: content-addressed runner scripts bundle. The deploy job builds
    # runner/ + runner-contract/schemas, names the archive by its md5 and
    # injects these fields; the api serves it through a proxy so prepare and
    # execute no longer clone the platform repository.
    runner_scripts_bundle_name: str | None = None
    runner_scripts_bundle_md5: str | None = None
    runner_scripts_bundle_sha256: str | None = None
    runner_scripts_bundle_size: int = 0

    # M5-15 dataset import uploads. Source files are staged in a temporary
    # directory (never in the database) and parsed by an isolated subprocess
    # with a wall-clock timeout and address-space limit; the stale window
    # lets the worker mark a crashed run as failed instead of spinning.
    dataset_import_dir: str = "/tmp/eval-dataset-imports"
    dataset_import_max_bytes: int = 1024 * 1024 * 1024
    dataset_import_timeout_seconds: int = 1800
    dataset_import_memory_limit_mb: int = 2048
    dataset_import_stale_seconds: int = 7200
    dataset_import_error_limit: int = 200
    dataset_import_progress_step: int = 1000
    # M8-25: ordered public registries probed for per-case environment images.
    # Comma-separated host[/namespace] entries; the default is Docker Hub.
    dataset_image_registries: str = "docker.io"
    dataset_image_arch: str = "x86_64"
    dataset_image_probe_timeout_seconds: float = 8.0
    dataset_image_probe_concurrency: int = 8
    # When enabled (the production default), an empty source list is a hard
    # configuration error and blocks image-bearing datasets. Tests and
    # non-image adapters can explicitly disable the check.
    dataset_image_check_enabled: bool = True
    # M8 (#290): a prepare worker claims one image at a time. The lease keeps a
    # crashed claim reclaimable instead of leaving the row stuck in preparing.
    # It must outlast one full pull+save+upload attempt (1800 + 1800 + 3600s);
    # a shorter lease lets a second worker steal a row mid-pull. Multi-worker
    # setups still need lease renewal/owner IDs, so the startup sweep is only
    # safe for a single worker.
    dataset_image_prepare_lease_seconds: int = 7200
    # The worker itself runs only when enabled; the compose ``image-prep``
    # profile is the deploy gate so a normal deploy never starts it before the
    # data disk exists. Pull/save happens in a dedicated Docker-in-Docker
    # daemon whose data root and this work dir both live on the data disk.
    dataset_image_prep_enabled: bool = True
    dataset_image_prep_work_dir: str = "/var/lib/ai-eval-image-prep"
    dataset_image_prep_docker_bin: str = "docker"
    dataset_image_prep_platform: str = "linux/amd64"
    # Refuse to pull unless the data disk has at least this much free space;
    # one 4.16GB image plus its tar cannot fit on the system disk.
    dataset_image_prep_min_free_bytes: int = 8 * 1024**3
    dataset_image_prep_poll_seconds: float = 5.0
    # M8 (#439): image-level concurrency. The production compose profile sets 2;
    # the code default stays 1 so direct runs keep the historical serial path.
    # Values are clamped to 1..4 by the worker.
    dataset_image_prep_concurrency: int = 1
    # Probe each target image's manifest on every configured source before the
    # pull, then try the fastest healthy source first and skip sources that
    # explicitly do not carry the image. Off by default so tests and direct runs
    # stay offline; the production compose profile enables it.
    dataset_image_prep_source_probe_enabled: bool = False
    dataset_image_prep_source_probe_timeout_seconds: float = 4.0
    dataset_image_prep_source_probe_concurrency: int = 8
    dataset_image_prep_heartbeat_path: str = "/tmp/image-prep-heartbeat"
    # M8 (#436): the DB heartbeat is what the API/launch gate reads. The worker
    # writes it every 30s, so a 120s window survives a slow pull without
    # declaring a healthy worker dead. A missing/expired row is reported as
    # "image prep service is not running" instead of an endless preparing 409.
    dataset_image_prep_heartbeat_timeout_seconds: int = 120
    dataset_image_prep_max_attempts: int = 3
    # A preparing row whose ``next_attempt_at`` has been due for this long was
    # enqueued while no worker was claiming rows (profile not started or the
    # worker crashed before its claim). The API reaper turns it into a visible
    # failed row instead of an endless "preparing" spinner.
    dataset_image_prep_stale_seconds: int = 1800

    # M2-05 synthetic evaluation loop.
    # Short-term case-token secret (platform signs, runner verifies). When
    # unset the case endpoints are open (development only).
    runner_task_secret: str | None = None
    # Base URL the runner reaches for callbacks/manifests. The fixed runner
    # shares the API host, so loopback is the default.
    runner_callback_base_url: str = "http://127.0.0.1:8000"
    # M8-14: forward proxy the runner injects into evaluation containers for
    # GitHub egress. Empty keeps the previous direct-network behaviour; when
    # set it must be a plain http(s) URL without embedded credentials.
    runner_egress_proxy: str | None = None
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
    # M8-34: secret configured on the GitHub webhook for ``workflow_job``
    # events. The API verifies X-Hub-Signature-256 with it and only marks active
    # spot pools due; no provider call happens in the request. Empty disables
    # the endpoint (503) so an unconfigured deployment cannot be woken by an
    # unauthenticated caller.
    github_webhook_secret: str | None = None
    # M5-18: deployments page reads and controls the production environment
    # recorded by the deploy workflow (DEPLOY_ENVIRONMENT in .env).
    deploy_environment: str = "production"
    deploy_rollback_event_type: str = "deployment-rollback"
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
    # cheap even when a large queue arrives at once). M8-34 makes this a fast
    # fallback poll: the GitHub ``workflow_job`` webhook wakes the controller
    # immediately, while 30s keeps the queue moving when a webhook is lost.
    # Override with EVAL_CAPACITY_CHECK_SECONDS.
    eval_capacity_check_seconds: int = 30
    eval_capacity_batch_size: int = 5
    # M5-19 resource-pool console: "today's resource cost" aggregates over a
    # local calendar day. Operators are in China (UTC+8), so the default window
    # is Asia/Shanghai while every stored timestamp stays UTC.
    resource_cost_utc_offset_hours: int = Field(default=8, ge=-12, le=14)
    # M4-04 Aliyun ECS spot provisioner. Aliyun quotes CNY/hour and the pool
    # threshold is stored in the same unit (M5-10), so scheduling compares them
    # directly. Credentials come from the encrypted resource connection, never
    # from these settings.
    aliyun_ecs_endpoint: str = "https://ecs.aliyuncs.com"
    aliyun_default_region: str = "cn-hangzhou"
    aliyun_default_instance_type: str = "ecs.g7.large"
    # A spot instance is reclaimed when it never starts or never registers a
    # runner; queued attempts stay queued and the next tick retries.
    eval_spot_register_timeout_seconds: int = 1200
    # Grace before an instance whose ephemeral runner exited is reclaimed. A
    # one-job runner stops heartbeating when it exits. M8-34 used 60s to free
    # capacity fast, but production showed cold start takes 2–3 minutes and a
    # single missed heartbeat then killed a healthy instance; M8-48 (#313)
    # raises the default to 180s so a short heartbeat jitter is absorbed while
    # a real exit still frees capacity within a few minutes. Instances that
    # never register still use eval_spot_register_timeout_seconds. Override
    # with EVAL_SPOT_RUNNER_LOST_GRACE_SECONDS.
    eval_spot_runner_lost_grace_seconds: int = 180
    # M8-48 (#313): do not scale down a runner registered less than this many
    # seconds ago. A zero-resident pool otherwise retires the instance in the
    # same tick that it registers, before the waiting shard can bind to it.
    # Only the registration age is guarded; a runner that just finished a job
    # still scales down immediately.
    eval_spot_runner_min_idle_seconds: int = 120
    # M8-46 (#311): after N consecutive provider-side spot reclaims, or N
    # consecutive reconcile ticks where inventory is missing or the spot price
    # is above the pool threshold, a run moves to the fixed/on-demand pool
    # configured as ``fallback_pool_id`` on the spot pool. The per-pool values
    # win; these defaults keep the feature bounded when a pool leaves them
    # unset. A cost cap compares the fallback pool's declared hourly price and
    # suppresses the switch when the price is unknown or above the cap.
    eval_spot_fallback_after: int = 3
    eval_spot_fallback_max_price_cny: Decimal | None = None
    # M4-02 execution lifecycle timeouts (design 06 §7). Five independent
    # clocks instead of one total timeout: platform queue, agent execution,
    # scoring, upload grace period and heartbeat. The agent execution timeout
    # is per run (``run.case_timeout_seconds``); the value here is only the
    # fallback for a run without one.
    # M8-45 (#307): the queue timeout is a shard progress lease, not a fixed
    # wall clock. Keep ``eval_queue_timeout_seconds`` only as the fallback for
    # rows written before the lease columns existed. New rows get
    # ``case_timeout + grace`` per renewal, capped at the absolute ceiling
    # (23h, below GitHub's 24h self-hosted job queue limit).
    eval_queue_timeout_seconds: int = 3600
    eval_queue_timeout_grace_seconds: int = 600
    eval_queue_timeout_max_seconds: int = 82800
    eval_execution_timeout_seconds: int = 900
    eval_scoring_timeout_seconds: int = 900
    eval_upload_grace_seconds: int = 300
    eval_heartbeat_timeout_seconds: int = 300
    # Attempts per lifecycle sweep; keeps one worker pass bounded.
    eval_lifecycle_batch_size: int = 100
    # Cancellation grace period (M4-02 A2). Running/scoring/uploading attempts
    # stay alive so their artifacts can settle; after this window the reconciler
    # force-cancels whatever is left so a cancelled run can never hang forever.
    # M8-43 (#304): the runner now sends a terminal callback on SIGTERM, so the
    # grace only needs to cover that callback round-trip, not a full 10-minute
    # wait after GitHub confirmed the cancel.
    eval_cancel_grace_seconds: int = 60
    # Budget pause grace period (M4-05, design 06 §8). Interrupted attempts may
    # upload their existing artifacts; after this window the reconciler settles
    # whatever is left as ``budget_paused`` so the run can reach paused_budget.
    eval_budget_pause_grace_seconds: int = 600
    # M5-04 launch preparations (design 04 §3.1). Prepare returns a short-lived,
    # single-use snapshot; the operator confirms it instead of re-posting the
    # whole configuration. The TTL is deliberately short because it freezes an
    # asset commit and the resolved capacity context.
    eval_preparation_ttl_seconds: int = 900
    # M8 (#290): image preparation is an out-of-band, multi-minute job. While a
    # frozen snapshot still has images that are not ready, prepare extends the
    # snapshot TTL to this longer value so the operator can confirm the launch
    # after the worker finishes instead of losing the snapshot mid-preparation.
    eval_preparation_image_ttl_seconds: int = 3600
    # Execution policy bounds accepted by prepare/create. The wizard renders
    # these as field errors; the server is the authority (never a fake success).
    eval_min_case_timeout_seconds: int = 60
    eval_max_case_timeout_seconds: int = 86400
    eval_max_case_attempts: int = 5
    # Cost-range estimate shown on the confirmation step. These are documented
    # modelling assumptions, never billing data: the UI labels the basis and
    # the range, and the gateway remains the spend authority.
    eval_estimate_input_tokens_per_case: int = 120000
    eval_estimate_output_tokens_per_case: int = 8000
    eval_estimate_case_seconds: int = 1200
    eval_estimate_spread: float = 0.3

    # M3-02 centralized model gateway. Credentials are encrypted with a
    # host-local Fernet key (SECRET_BOX_KEY); the gateway signs short-lived
    # runner tokens with GATEWAY_TOKEN_SECRET. Both are required in
    # production (the deploy script generates them on first rollout).
    secret_box_key: str = ""
    gateway_token_secret: str | None = None
    gateway_token_ttl_seconds: int = 900
    # M8 (#358): shard-level model tokens exist only while old runner scripts
    # drain. Set an ISO-8601 instant to retire them; empty keeps the
    # compatibility branch (all-terminal shard rule) enabled. New tokens are
    # always case-scoped.
    gateway_legacy_token_deadline: datetime | None = None
    gateway_timeout_seconds: float = 120.0
    # Connection-level retries for provider calls (bounded; never retries a
    # stream that has already started).
    gateway_connect_retries: int = 2
    # M3-04 execution/scoring image digests exposed to the launch wizard.
    default_execution_image_digest: str | None = None
    default_scoring_image_digest: str | None = None

    # M9 self-evolution reporting (#263). The signing key authorizes the
    # session-envelope endpoint consumed by harness-evolution (short-lived
    # per-report HMAC); the public base URL is where that platform reaches
    # this deployment. Both are optional: without them bad cases are still
    # reported, just without the sessionSource trajectory link. Long-lived
    # values are injected from GitHub Secrets like SECRET_BOX_KEY.
    self_evolution_session_signing_key: str | None = Field(
        default=None, validation_alias="SELF_EVO_SESSION_SIGNING_KEY"
    )
    self_evolution_public_base_url: str | None = Field(
        default=None, validation_alias="SELF_EVO_PUBLIC_BASE_URL"
    )
    # Worker reporting knobs: one pass claims at most this many pending rows.
    self_evolution_batch_size: int = Field(
        default=50, validation_alias="SELF_EVO_BATCH_SIZE"
    )

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:8080"

    @field_validator("gateway_legacy_token_deadline", mode="before")
    @classmethod
    def _empty_legacy_deadline(cls, value):
        """Treat an empty deploy env value as "compatibility still open"."""

        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("runner_egress_proxy")
    @classmethod
    def _validate_runner_egress_proxy(cls, value: str | None) -> str | None:
        """Accept only a credential-free http(s) proxy URL; empty means off."""

        if value is None:
            return None
        proxy = value.strip()
        if not proxy:
            return None
        parsed = urlparse(proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("RUNNER_EGRESS_PROXY must be an http(s) URL with a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("RUNNER_EGRESS_PROXY must not embed credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("RUNNER_EGRESS_PROXY must not include a query or fragment")
        return proxy

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
