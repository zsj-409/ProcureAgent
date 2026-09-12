"""Routes suppliers to the correct executor."""

from ..errors import ExecutorError
from ..infrastructure.settings import Settings
from ..suppliers.base import SupplierProfile, SupplierRegistry
from .api_executor import ApiExecutor
from .base import BaseExecutor
from .portal_executor import PortalExecutor


class ExecutorRouter:
    """Choose an executor based solely on a supplier's configured channel."""

    def __init__(self, settings: Settings, registry: SupplierRegistry):
        self._settings = settings
        self._registry = registry
        self._api_executor = ApiExecutor(settings.supplier_api_timeout_seconds)
        self._portal_executor = PortalExecutor(settings)

    def suppliers(self) -> list[SupplierProfile]:
        """Return the enabled supplier catalog."""

        return self._registry.enabled_profiles()

    def route(self, supplier: SupplierProfile) -> BaseExecutor:
        """Return the executor for a supplier."""

        if supplier.source_type == "api":
            return self._api_executor
        if supplier.source_type == "portal":
            return self._portal_executor
        raise ExecutorError(f"No executor for source_type {supplier.source_type!r}")
