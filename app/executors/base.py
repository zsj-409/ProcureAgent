"""Executor abstractions shared by API and web collection.

Phase 3: every executor declares its external-effect capability so the
recovery path can decide whether re-execution after a crash is safe.
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel

from ..suppliers.base import SupplierProfile


class ExternalCapability(str, Enum):
    """External side-effect capability of a tool/executor (Phase 3 contract).

    - READ_ONLY_RETRYABLE: no external mutation; bounded retry is safe and
      each invocation is counted as a duplicate QUERY, not a side effect.
    - REMOTE_IDEMPOTENT: mutates, but supports an external idempotency key;
      retry with the SAME key yields at most one committed effect.
    - RECONCILABLE_MUTATION: mutates; the external system exposes a request id
      that can be queried to learn the true outcome.
    - NON_IDEMPOTENT_MUTATION: mutates with no idempotency and no queryable
      outcome; on uncertainty MUST enter UNKNOWN_OUTCOME, never blind-retry.
    - UNKNOWN_CAPABILITY: conservative — treat as NON_IDEMPOTENT_MUTATION and
      require human verification.
    """

    READ_ONLY_RETRYABLE = "READ_ONLY_RETRYABLE"
    REMOTE_IDEMPOTENT = "REMOTE_IDEMPOTENT"
    RECONCILABLE_MUTATION = "RECONCILABLE_MUTATION"
    NON_IDEMPOTENT_MUTATION = "NON_IDEMPOTENT_MUTATION"
    UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"


class RetryPolicy(BaseModel):
    """Declared retry bounds for a tool (Phase 3)."""

    max_retries: int = 2
    retryable_error_codes: list[str] = ["timeout", "network", "5xx", "connection"]
    # True only for tools whose retry can never create a duplicate business
    # effect (read-only or verified idempotent).
    retry_safe: bool = False


class ExecutorTask(BaseModel):
    """A bounded collection request sent to an executor."""

    task_id: str
    supplier: SupplierProfile
    product_name: str
    quantity: int
    # Phase 3: the caller may attach an external idempotency key for
    # REMOTE_IDEMPOTENT tools; the executor passes it through.
    external_idempotency_key: str | None = None


class ExecutorResult(BaseModel):
    """The outcome of one executor attempt.

    ``quote`` is intentionally a raw mapping here; normalization happens later
    in ``QuoteNormalizer`` so executors never perform price math.
    """

    success: bool
    quote: dict[str, Any] | None = None
    error: str | None = None
    # Phase 3: external outcome metadata for reconciliation.
    external_request_id: str | None = None
    committed: bool | None = None  # True = external effect confirmed committed
    outcome_known: bool = True  # False = external outcome uncertain


class BaseExecutor(ABC):
    """Interface every supplier channel executor must implement."""

    # Phase 3: the capability contract every executor must declare.
    capability: ExternalCapability = ExternalCapability.UNKNOWN_CAPABILITY
    retry_policy: RetryPolicy = RetryPolicy(retry_safe=False)

    @abstractmethod
    async def execute(self, task: ExecutorTask) -> ExecutorResult:
        """Execute the bounded collection task."""
