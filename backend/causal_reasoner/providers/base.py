"""Provider-neutral generation request, deliberately separate from trusted citations.

Construction checks that the model view was derived from the *same* local raw
registry. It does not by itself confer consent to disclose repository content.
Only an authorized server-side caller may set ``external_processing_approved``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..answer_models import ReasonedAnswer
from ..evidence import EvidenceBundle, EvidenceKind
from ..redaction import (
    RedactedEvidenceBundle, RedactionPolicy, UnsafeEvidenceForModel,
    redact_evidence,
)

_ERROR = "Generation request did not satisfy the provider boundary."
_REQUEST_SEAL = object()


class GenerationBoundaryError(ValueError):
    """The request was not authenticated against the local evidence view."""


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    """Redacted evidence only; no raw indexed content or secret-bearing paths.

    A private marker ensures normal callers use ``prepare_generation_request``
    instead of constructing an unchecked model payload. This is an in-process
    invariant, NOT a substitute for API authorization or user consent.
    """

    prepared: RedactedEvidenceBundle = field(repr=False)
    external_processing_approved: bool = False
    _seal: object = field(default=None, repr=False, compare=False)


@runtime_checkable
class ReasoningProvider(Protocol):
    def generate(self, request: GenerationRequest) -> ReasonedAnswer:
        """Return a schema-valid draft, NOT a citation-validated answer."""
        ...


def prepare_generation_request(
    raw: EvidenceBundle,
    prepared: RedactedEvidenceBundle,
    *,
    external_processing_approved: bool = False,
    policy: RedactionPolicy | None = None,
) -> GenerationRequest:
    """Verify the redacted view belongs to the trusted local evidence registry.

    Never creates an API client or performs I/O. The caller must independently
    establish repository-owner consent and repository authorization before
    passing ``external_processing_approved=True``.
    """
    if (
        type(raw) is not EvidenceBundle
        or type(prepared) is not RedactedEvidenceBundle
        or type(external_processing_approved) is not bool
        or (policy is not None and type(policy) is not RedactionPolicy)
    ):
        raise GenerationBoundaryError(_ERROR)
    try:
        expected = redact_evidence(raw, policy=policy)
    except UnsafeEvidenceForModel:
        raise GenerationBoundaryError(_ERROR) from None
    if expected != prepared:
        raise GenerationBoundaryError(_ERROR)
    if not any(item.kind is EvidenceKind.INDEXED_SOURCE for item in prepared.items):
        # Do not send queries with no admissible indexed evidence to a model.
        raise GenerationBoundaryError(_ERROR)
    return GenerationRequest(prepared, external_processing_approved, (_REQUEST_SEAL, prepared))


def _assert_request(request: GenerationRequest) -> None:
    if (
        type(request) is not GenerationRequest
        or type(request._seal) is not tuple or len(request._seal) != 2
        or request._seal[0] is not _REQUEST_SEAL
        or request._seal[1] is not request.prepared
        or type(request.external_processing_approved) is not bool
        or type(request.prepared) is not RedactedEvidenceBundle
    ):
        raise GenerationBoundaryError(_ERROR)
