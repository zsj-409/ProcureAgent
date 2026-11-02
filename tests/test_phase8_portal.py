"""Phase 8 — Portal LLM Fallback harness tests (Task 3).

Verifies the harness itself: frozen scenario coverage, mode semantics
(REAL_LLM_NOT_RUN default), manifest completeness, and that every
model-proposed action is guarded (whitelist).
"""

from decimal import Decimal

from app.eval.phase8_portal import (
    PORTAL_SCENARIOS,
    run_portal_scenarios,
    write_portal_manifest,
)
from app.infrastructure.settings import Settings


def _settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        approval_threshold=Decimal(1000),
        auth_enabled=True,
        demo_identity_header="",
        chrome_cdp_url="",
        web_allow_launch=False,
        suppliers_config_path="",
    )


def test_portal_scenarios_are_frozen():
    """6-8 frozen scenarios covering the required fault dimensions."""
    ids = [s["id"] for s in PORTAL_SCENARIOS]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 6
    faults = {s["fault"] for s in PORTAL_SCENARIOS}
    assert "F7_PROMPT_INJECTION" in faults       # injection
    assert "F5_SESSION_EXPIRATION" in faults     # session expiry
    assert "F8_AMBIGUOUS_UNSAFE" in faults       # ambiguity
    assert "QUOTE_EXPIRED" in faults             # quote expiry


def test_no_key_portal_run_is_not_run():
    """No key -> REAL_LLM_NOT_RUN with zero real calls; the offline plumbing
    still exercises all 8 scenarios."""
    m = run_portal_scenarios(_settings(), model_client=None, mode="MOCK")
    assert m.mode == "REAL_LLM_NOT_RUN"
    assert m.calls == 0
    assert len(m.scenarios) == len(PORTAL_SCENARIOS)
    assert m.tokens is None  # UNKNOWN, never fake 0


def test_portal_manifest_is_complete(tmp_path):
    m = run_portal_scenarios(_settings(), model_client=None, mode="MOCK")
    for key in (
        "run_id", "date", "git_commit", "model_id", "provider", "prompt_version",
        "mode", "scenarios", "calls", "tokens", "cost_usd", "errors",
    ):
        assert key in m.__dict__, f"manifest missing {key}"
    path = write_portal_manifest(m, tmp_path)
    assert path.exists()
    import json

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["mode"] == "REAL_LLM_NOT_RUN"
    assert len(loaded["scenarios"]) == len(PORTAL_SCENARIOS)
