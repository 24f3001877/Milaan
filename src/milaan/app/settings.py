"""Application settings.

Twelve-factor: all configuration from the environment, validated at boot. A missing or
malformed variable fails fast and loudly here rather than mysteriously mid-run
(Appflow §4.4). `APP_ENV` gates dangerous capabilities — dev-only routes are refused at
route-registration time, not by a runtime `if` check, so there is no path that accidentally
ships a debug endpoint to the demo environment.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppEnv(StrEnum):
    development = "development"
    ci = "ci"
    demo = "demo"


class LLMMode(StrEnum):
    live = "live"
    cached = "cached"
    disabled = "disabled"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="forbid")

    app_env: AppEnv
    api_token: str = Field(min_length=8)

    database_url: str
    database_url_sync: str

    redis_url: str
    celery_broker_url: str
    celery_result_backend: str

    llm_mode: LLMMode = LLMMode.cached
    llm_provider: str = "gemini"
    llm_api_key: str = ""
    llm_model: str = "gemini-1.5-flash"
    llm_prompt_version: str = "v1"

    dev_seed_enabled: bool = False

    log_level: str = "INFO"
    log_format: str = "json"

    # --- Deployment ---------------------------------------------------------------------
    # Browser origins allowed to call the API, comma-separated. The default is the Vite dev
    # server, which is what local development needs and the only value the app used to
    # accept — it was a literal in main.py, so a deployed frontend on any other origin was
    # refused with no way to configure it. A same-origin deployment (SPA served by this
    # process, below) needs no entry here at all.
    cors_origins: str = "http://localhost:5173"

    # Directory holding the built SPA (`npm run build` output). When it exists, this process
    # serves the frontend at `/` alongside the API, so a deployment is one origin and one
    # service: no CORS, no second host, and — the constraint that actually forces it —
    # `data/uploads` stays on one filesystem, which the worker needs since it reads the
    # files the API wrote there (app/api/runs.py, app/tasks/run_task.py).
    static_dir: str = "frontend/dist"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def dev_seed_actually_enabled(self) -> bool:
        """AND of the flag and the environment — dev/seed can never exist outside development,
        no matter what DEV_SEED_ENABLED is set to elsewhere."""
        return self.dev_seed_enabled and self.app_env == AppEnv.development


def get_settings() -> Settings:
    # Instantiated fresh (not cached at import time) so a missing var fails at first use
    # with a clear Pydantic ValidationError rather than at an arbitrary later moment.
    return Settings()  # type: ignore[call-arg]
