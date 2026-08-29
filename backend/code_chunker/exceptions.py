"""Fatal errors raised by Code Chunker contract validation."""


class CodeChunkerError(Exception):
    """Base class for fatal Code Chunker errors."""


class ChunkerConfigurationError(CodeChunkerError):
    """Raised when chunker configuration is invalid."""


class InvalidChunkInventory(CodeChunkerError):
    """Raised when input inventories violate the chunker contract."""


class RepositoryChunkError(CodeChunkerError):
    """Raised for fatal repository-level chunking failures."""
