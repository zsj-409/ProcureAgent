"""Approval write-guard for Phase 1.

Enforces, with deterministic code:

1. An unauthenticated request can never approve or reject a task.
2. A body-supplied ``approver`` field is never trusted as identity.
3. When full authentication is not configured, approval writes default to
   **deny**; the only escape hatch is the explicitly-isolated demo provider
   (header allowlist, never a bare env flag).
4. A denied request must not mutate task state nor write a success approval
   record, and the caller gets a machine-readable ``reason_code``.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..infrastructure.settings import Settings
from .principal import (
    DemoIdentityProvider,
    Principal,
    Role,
)


@dataclass(frozen=True)
class ApprovalGuardDecision:
    """Result of the approval guard for a write attempt."""

    allowed: bool
    reason_code: str | None = None
    principal: Principal | None = None
    http_status: int = 200


class ApprovalGuard:
    """Deterministic gate in front of approve/reject write endpoints."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings
        self._demo_provider = None
        if settings is not None and not settings.auth_enabled and settings.demo_identity_header:
            self._demo_provider = DemoIdentityProvider(
                header_name=settings.demo_identity_header,
                default_principal=DEMO_APPROVER_PRINCIPAL,
            )

    def resolve_principal(self, request: Request) -> Principal | None:
        """Resolve the caller from the request.

        Phase 1 has no real token/OIDC layer. If ``auth_enabled`` is True we
        have nothing to authenticate with, so we treat the caller as
        unauthenticated (deny). The demo provider is the only way to get a
        principal, and only in the isolated demo configuration.
        """

        if self._settings is None:
            return None
        if self._settings.auth_enabled:
            # No real identity layer yet -> no authenticated caller.
            return None
        if self._demo_provider is not None:
            return self._demo_provider.resolve(request)
        return None

    def check(self, request: Request, *, required_role: Role | None = None) -> ApprovalGuardDecision:
        """Gate an approval write.

        Rules (in order):
        - unauthenticated -> 401 AUTH_REQUIRED (default deny)
        - role required but principal lacks it -> 403 FORBIDDEN
        - body-supplied ``approver`` is ignored entirely (identity comes from
          the resolved principal only)
        """

        principal = self.resolve_principal(request)
        if principal is None:
            return ApprovalGuardDecision(
                allowed=False,
                reason_code="AUTH_REQUIRED",
                http_status=401,
            )
        if required_role is not None and principal.role != required_role:
            return ApprovalGuardDecision(
                allowed=False,
                reason_code="FORBIDDEN",
                principal=principal,
                http_status=403,
            )
        return ApprovalGuardDecision(allowed=True, principal=principal)


# The canonical demo approver used only by the isolated demo environment.
from .principal import DEMO_APPROVER as DEMO_APPROVER_PRINCIPAL  # noqa: E402
