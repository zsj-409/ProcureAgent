"""Domain-level exceptions used across ProcureAgent."""


class ProcureAgentError(Exception):
    """Base class for expected ProcureAgent failures."""


class ExecutorError(ProcureAgentError):
    """Raised when a supplier executor cannot complete its work."""


class SupplierUnavailableError(ExecutorError):
    """Raised when a supplier cannot be reached or returns no usable quote."""


class WebExecutionError(ExecutorError):
    """Raised when the WebExecutor fails a bounded action sequence."""


class ValidationError(ProcureAgentError):
    """Raised when procurement data fails deterministic validation."""


class PolicyError(ProcureAgentError):
    """Raised when an approval policy rule cannot be evaluated."""


class ModelClientError(ProcureAgentError):
    """Raised when the optional model client cannot produce a response."""


class InterpretationError(ProcureAgentError):
    """Raised when a natural-language message cannot be interpreted."""
