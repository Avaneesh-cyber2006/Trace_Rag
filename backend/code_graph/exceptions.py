"""Exceptions raised by the Code Graph Builder."""


class CodeGraphError(Exception):
    """Base exception for code graph failures."""


class CodeGraphValidationError(CodeGraphError):
    """Raised when upstream inventory input is malformed or inconsistent."""


class CodeGraphIdentityError(CodeGraphError):
    """Raised when a graph identity cannot be constructed safely."""


class CodeGraphInvariantError(CodeGraphError):
    """Raised when a graph value violates an immutable contract."""


class CodeGraphResolutionError(CodeGraphError):
    """Raised when a resolver violates its internal contract."""
