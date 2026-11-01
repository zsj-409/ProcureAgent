"""Identity primitives for the Phase-1 approval guard.

Phase 1 deliberately does NOT implement a full RBAC/OIDC/multi-tenant system.
This module provides:

- a typed ``Principal`` (the authenticated caller),
- a small, explicit ``DemoIdentityProvider`` for the isolated offline/demo
  environment only (it is never activated by default and never via a bare
  ``PROCUREAGENT_AUTH_ENABLED=false`` without an explicit demo mode flag),
- helpers that resolve the caller from the request.

The hard rule enforced by ``approval_guard.py`` is: **an unauthenticated
request can never approve or reject a task**, and an ``approver`` field coming
from the request body is never trusted as identity.
"""

from dataclasses import dataclass, field
from enum import Enum

from fastapi import Request


class Role(str, Enum):
    """The four roles the V3 roadmap defines (Phase 3 will make them real)."""

    REQUESTER = "requester"
    PROCUREMENT_OPERATOR = "procurement_operator"
    APPROVER = "approver"
    AUDITOR = "auditor"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller. Identity is only ever set from auth, never
    from the request body."""

    id: str
    role: Role
    tenant: str = "default"
    # When true, this principal is a controlled test/demo identity used only
    # in an explicitly isolated environment (see DemoIdentityProvider).
    is_demo: bool = False
    # Optional structured metadata the auth layer attaches (Phase 3 will
    # extend this with token claims etc.).
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role.value,
            "tenant": self.tenant,
            "is_demo": self.is_demo,
            "metadata": self.metadata,
        }


class AuthNotConfiguredError(Exception):
    """Raised when a request requires authentication but no identity provider
    is configured for the request."""


class DemoIdentityProvider:
    """Controlled identity for the offline demo/test environment.

    Activated ONLY when BOTH of these hold (see settings):
      - ``auth_enabled`` is False (i.e. the operator explicitly disabled
        authentication for a demo), AND
      - ``demo_identity_header`` is set (a non-default, explicitly-chosen
        header name). A bare env flag alone can never enable demo identity.
    """

    def __init__(self, header_name: str, default_principal: Principal):
        self._header_name = header_name
        self._default = default_principal

    def resolve(self, request: Request) -> Principal | None:
        """Return the principal carried by the demo header, or None.

        The header value is matched against a hard-coded allowlist of demo
        principals; a random header value can never forge a principal.
        """

        value = request.headers.get(self._header_name)
        if not value:
            return None
        # Only the configured demo principal id is honored.
        if value.strip() == self._default.id:
            return self._default
        return None


# The canonical demo approver used only by the isolated demo environment.
DEMO_APPROVER = Principal(
    id="demo-approver",
    role=Role.APPROVER,
    tenant="demo",
    is_demo=True,
)
