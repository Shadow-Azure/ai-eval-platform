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

    default_tenant_slug: str = "default"
    default_workspace_slug: str = "default"
    default_project_slug: str = "default"

    worker_poll_seconds: float = 5.0
    worker_heartbeat_path: str = "/tmp/eval-worker-heartbeat"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173,http://127.0.0.1:4173,http://localhost:8080"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
