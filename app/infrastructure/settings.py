"""Application configuration.

All ports and URLs used by the project are defined here so that no component
hard-codes an address. Values can be overridden with environment variables
prefixed by ``PROCUREAGENT_`` or with a local ``.env`` file.
"""

from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for ProcureAgent."""

    model_config = SettingsConfigDict(
        env_prefix="PROCUREAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ProcureAgent"
    environment: str = "development"

    # SQLite is the stage-one persistence layer and does not need a network port.
    database_url: str = "sqlite:///./procure_agent.db"

    supplier_api_base_url: str = "http://localhost:8101"
    supplier_portal_url: str = "http://localhost:8102"
    chrome_cdp_url: str = "http://localhost:9222"
    suppliers_config_path: str = "config/suppliers.json"

    executor_timeout_seconds: float = 15.0
    supplier_api_timeout_seconds: float = 10.0
    web_retries: int = 2
    web_headless: bool = True
    web_allow_launch: bool = True

    # Portal steps launch a real browser; they need a larger slice of the
    # task budget than API calls. The orchestrator uses this per portal step.
    portal_step_timeout_seconds: float = 40.0
    api_step_timeout_seconds: float = 15.0

    # Adaptive portal agent: escalation ladder fixed-script → heuristic → LLM.
    portal_adaptive: bool = True
    portal_max_actions: int = 10
    portal_frames: bool = True
    portal_frames_dir: str = "data/portal_frames"

    # Concurrent supplier collection (bounded by a semaphore in the orchestrator).
    collect_concurrency: int = 4

    # LLM is an optional enhancement. When no key is configured the system uses
    # RuleBasedPlanner and deterministic templates.
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0

    # --- Phase 8 scoped real-LLM validation -------------------------------
    # Explicit opt-in ONLY. Defaults are conservative: no key, no budget,
    # no real API calls. A real request is only ever made when BOTH a key
    # AND an explicit sandbox-only flag are set by the operator.
    llm_sandbox_only: bool = False   # Phase 8: only mock/offline validation
    llm_max_calls: int = 20          # hard cap per validation run
    llm_max_tokens: int = 2000       # hard cap per call (best-effort)
    llm_retry_limit: int = 1         # retries per call
    llm_cost_budget_usd: float = 5.0 # hard budget for a Phase 8 run

    # Runtime execution budget.
    agent_max_steps: int = 20
    agent_max_attempts: int = 2
    agent_max_task_seconds: float = 150.0
    agent_max_replans: int = 1

    # Purchases at or above this total require human approval.
    approval_threshold: Decimal = Decimal(1000)

    # --- Phase 10 OpenTelemetry AgentOps --------------------------------
    # In-memory by default (offline demo); OTLP export only when explicitly
    # enabled. Exporter failure must never break procurement tasks.
    otel_enabled: bool = True
    otel_export_enabled: bool = False

    # --- Phase 1 approval security ---------------------------------------
    # When True (default), approval/reject writes require an authenticated
    # caller. There is no real identity layer yet, so with auth_enabled=True
    # every approval write is DENIED (default deny). To run the offline demo,
    # set auth_enabled=False AND demo_identity_header to an explicitly chosen
    # header name; the demo identity is then resolved from that header only
    # (allowlist, never trusted from the request body). A bare env flag can
    # never enable anonymous approval.
    auth_enabled: bool = True
    demo_identity_header: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings()
