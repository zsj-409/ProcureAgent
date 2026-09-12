"""Executor abstractions shared by API and web collection."""

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from ..suppliers.base import SupplierProfile


class ExecutorTask(BaseModel):
    """A bounded collection request sent to an executor."""

    task_id: str
    supplier: SupplierProfile
    product_name: str
    quantity: int


class ExecutorResult(BaseModel):
    """The outcome of one executor attempt.

    ``quote`` is intentionally a raw mapping here; normalization happens later
    in ``QuoteNormalizer`` so executors never perform price math.
    """

    success: bool
    quote: dict[str, Any] | None = None
    error: str | None = None


class BaseExecutor(ABC):
    """Interface every supplier channel executor must implement."""

    @abstractmethod
    async def execute(self, task: ExecutorTask) -> ExecutorResult:
        """Execute the bounded collection task."""
