"""Exception hierarchy for the runtime evidence package."""


class RuntimeEvidenceError(Exception):
    """Base class for all runtime evidence errors."""


class RuntimeEvidenceConfigurationError(RuntimeEvidenceError):
    """Raised when runtime evidence configuration is invalid."""


class RuntimeEvidenceValidationError(RuntimeEvidenceError):
    """Raised when runtime evidence input fails validation."""


class RuntimeEvidenceLimitError(RuntimeEvidenceError):
    """Raised when a runtime evidence limit is exceeded."""


class RuntimeEvidenceGraphError(RuntimeEvidenceError):
    """Raised when runtime evidence graph data is invalid."""


class RuntimeEvidenceInvariantError(RuntimeEvidenceError):
    """Raised when a runtime evidence invariant is violated."""


class RuntimeEvidenceIngestionError(RuntimeEvidenceError):
    """Raised when runtime evidence ingestion fails."""
