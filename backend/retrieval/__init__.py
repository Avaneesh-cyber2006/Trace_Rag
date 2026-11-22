"""Module 9 retrieval primitives (lexical foundation)."""

from .errors import (
    InvalidHybridEvidence, InvalidLexicalIndex, InvalidRetrievalQuery, RetrievalError,
)
from .lexical import LexicalDocument, LexicalHit, LexicalIndex
from .hybrid import HybridHit, HybridRanker, SemanticCandidate
from .relevance import RelevanceGate, RelevancePolicy
from .history import HistoricalChange, PathHistory, HistoricalContext, HistoryAnnotator

__all__ = (
    "HybridHit",
    "HybridRanker",
    "GraphContext",
    "GraphExpander",
    "HybridRetriever",
    "GraphRetrievalResult",
    "GraphRevision",
    "build_graph_revision",
    "InvalidHybridEvidence",
    "InvalidLexicalIndex",
    "InvalidRetrievalQuery",
    "LexicalDocument",
    "LexicalHit",
    "LexicalIndex",
    "RetrievalError",
    "SemanticCandidate",
    "RelevanceGate",
    "RelevancePolicy",
    "HistoricalChange",
    "PathHistory",
    "HistoricalContext",
    "HistoryAnnotator",
    "HistoricalRetrievalResult",
    "RuntimeFrameClue",
    "RuntimePathContext",
    "RuntimeContext",
    "RuntimeAnnotator",
    "RuntimeRetrievalResult",
)


def __getattr__(name: str):
    """Keep pure lexical/rank APIs importable without optional Chroma SDKs."""
    if name in ("GraphContext", "GraphExpander"):
        from .graph import GraphContext, GraphExpander

        return {"GraphContext": GraphContext, "GraphExpander": GraphExpander}[name]
    if name in ("RuntimeFrameClue", "RuntimePathContext", "RuntimeContext", "RuntimeAnnotator"):
        from .runtime import RuntimeFrameClue, RuntimePathContext, RuntimeContext, RuntimeAnnotator

        return {
            "RuntimeFrameClue": RuntimeFrameClue, "RuntimePathContext": RuntimePathContext,
            "RuntimeContext": RuntimeContext, "RuntimeAnnotator": RuntimeAnnotator,
        }[name]
    if name in ("HybridRetriever", "GraphRetrievalResult", "HistoricalRetrievalResult", "RuntimeRetrievalResult"):
        from .orchestrator import HybridRetriever, GraphRetrievalResult, HistoricalRetrievalResult, RuntimeRetrievalResult

        return {
            "HybridRetriever": HybridRetriever, "GraphRetrievalResult": GraphRetrievalResult,
            "HistoricalRetrievalResult": HistoricalRetrievalResult,
            "RuntimeRetrievalResult": RuntimeRetrievalResult,
        }[name]
    if name in ("GraphRevision", "build_graph_revision"):
        from .provenance import GraphRevision, build_graph_revision

        return {"GraphRevision": GraphRevision, "build_graph_revision": build_graph_revision}[name]
    raise AttributeError(name)
