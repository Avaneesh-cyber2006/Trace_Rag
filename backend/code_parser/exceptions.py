"""Fatal exceptions raised by the Code Parser."""


class CodeParserError(Exception):
    """Base class for fatal Code Parser failures."""


class InvalidParseInventory(CodeParserError):
    """Raised when FileInventory-level invariants are invalid."""


class ParserConfigurationError(CodeParserError):
    """Raised when the closed parser registry is invalid."""


class RepositoryParseError(CodeParserError):
    """Raised when a safe repository-root boundary cannot be established."""
