"""Public, content-free failures for Module 9 retrieval contracts."""


class RetrievalError(Exception):
    """Base error for repository retrieval."""


class InvalidLexicalIndex(RetrievalError, ValueError):
    """The caller supplied invalid or unbounded lexical source evidence."""


class InvalidRetrievalQuery(RetrievalError, ValueError):
    """A lexical search request violated its public contract."""


class InvalidHybridEvidence(RetrievalError, ValueError):
    """Ranked evidence is malformed, foreign to, or stale for the snapshot."""
