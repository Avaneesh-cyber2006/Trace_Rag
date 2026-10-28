"""Runtime evidence package."""

from __future__ import annotations

import typing as _typing

from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError,
    RuntimeEvidenceError,
    RuntimeEvidenceGraphError,
    RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError,
    RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor
from backend.runtime_evidence.models import (
    CaptureCompleteness,
    FrameAssociation,
    GraphBinding,
    PathMatchBasis,
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeArtifactRecord,
    RuntimeArtifactStatus,
    RuntimeEvent,
    RuntimeEventKind,
    RuntimeEvidenceInventory,
    RuntimeEvidenceIssue,
    RuntimeEvidenceRequest,
    RuntimeFrameLanguage,
    RuntimeIncompleteReason,
    RuntimeIssueCode,
    RuntimeStackFrame,
    RuntimeTag,
)

if _typing.TYPE_CHECKING:
    from backend.code_graph import CodeGraphInventory

__all__ = (
    "CaptureCompleteness",
    "FrameAssociation",
    "GraphBinding",
    "PathMatchBasis",
    "RuntimeArtifact",
    "RuntimeArtifactFormat",
    "RuntimeArtifactRecord",
    "RuntimeArtifactStatus",
    "RuntimeEvent",
    "RuntimeEventKind",
    "RuntimeEvidenceConfig",
    "RuntimeEvidenceConfigurationError",
    "RuntimeEvidenceError",
    "RuntimeEvidenceGraphError",
    "RuntimeEvidenceIngestionError",
    "RuntimeEvidenceIngestor",
    "RuntimeEvidenceInventory",
    "RuntimeEvidenceInvariantError",
    "RuntimeEvidenceIssue",
    "RuntimeEvidenceLimitError",
    "RuntimeEvidenceRequest",
    "RuntimeEvidenceValidationError",
    "RuntimeFrameLanguage",
    "RuntimeIncompleteReason",
    "RuntimeIssueCode",
    "RuntimeStackFrame",
    "RuntimeTag",
    "ingest_runtime_evidence",
)


def ingest_runtime_evidence(
    request: RuntimeEvidenceRequest,
    graph: CodeGraphInventory | None = None,
    config: RuntimeEvidenceConfig | None = None,
) -> RuntimeEvidenceInventory:
    """Ingest runtime evidence with an isolated default ingestor."""
    return RuntimeEvidenceIngestor(config).ingest(request, graph)
