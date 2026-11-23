"""Citation checks against the redacted *and* original local evidence registry."""

from __future__ import annotations

from dataclasses import dataclass, field

from .answer_models import (
    AnswerStatus, ClaimCategory, InvalidReasonedAnswer, ReasonedAnswer,
    parse_answer,
)
from .evidence import EvidenceBundle, EvidenceKind
from .redaction import (
    RedactedEvidenceBundle, RedactedEvidenceItem, RedactionPolicy,
    UnsafeEvidenceForModel, redact_evidence,
)


_CATEGORY_KIND = {
    ClaimCategory.INDEXED_FACT: EvidenceKind.INDEXED_SOURCE,
    ClaimCategory.GRAPH_RELATION: EvidenceKind.REVISION_BOUND_GRAPH,
    ClaimCategory.REPORTED_HISTORY: EvidenceKind.UNVERIFIED_GIT_HISTORY,
    ClaimCategory.REPORTED_RUNTIME: EvidenceKind.UNVERIFIED_RUNTIME,
}


@dataclass(frozen=True, slots=True)
class ValidatedReasonedAnswer:
    """Registry-vetted citation references; NOT a semantic truth guarantee."""

    answer: ReasonedAnswer = field(repr=False)
    citations: tuple[RedactedEvidenceItem, ...] = field(repr=False)


def validate_answer(
    raw: EvidenceBundle,
    redacted: RedactedEvidenceBundle,
    candidate: str | dict,
    *,
    redaction_policy: RedactionPolicy | None = None,
) -> ValidatedReasonedAnswer:
    """Verify only admitted request-local references, kinds, and exact excerpts.

    Never uses source files, Chroma, Git, network, or model SDK. The server must
    supply ``raw`` from a trusted Module 9 result, not from browser inputs.
    """
    if type(raw) is not EvidenceBundle or type(redacted) is not RedactedEvidenceBundle:
        raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
    try:
        independently_redacted = redact_evidence(raw, policy=redaction_policy)
    except UnsafeEvidenceForModel:
        raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.") from None
    if independently_redacted != redacted:
        raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
    answer = parse_answer(candidate)
    all_items = {item.label: item for item in redacted.items}
    used: set[str] = set()

    def get(label: str) -> RedactedEvidenceItem:
        if type(label) is not str or label not in all_items:
            raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
        used.add(label)
        return all_items[label]

    for claim in answer.claims:
        if type(claim.category) is not ClaimCategory or not claim.citations:
            raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
        evidence_items = tuple(get(label) for label in claim.citations)
        accepted_kind = _CATEGORY_KIND.get(claim.category)
        if accepted_kind is not None and any(item.kind is not accepted_kind for item in evidence_items):
            raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
        if claim.excerpt is not None:
            if claim.category is not ClaimCategory.INDEXED_FACT:
                raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
            if "[REDACTED]" in claim.excerpt or not any(
                claim.excerpt in item.content and item.kind is EvidenceKind.INDEXED_SOURCE
                for item in evidence_items
            ):
                raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
    for hypothesis in answer.hypotheses:
        for label in hypothesis.supporting_citations + hypothesis.contradicting_citations:
            get(label)
        if hypothesis.status.value == "possible" and not hypothesis.supporting_citations:
            raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
    if answer.status is AnswerStatus.ANSWERED and not any(
        all_items[label].kind in (EvidenceKind.INDEXED_SOURCE, EvidenceKind.REVISION_BOUND_GRAPH)
        for label in used
    ):
        # A history/runtime-only record cannot establish an indexed-source answer.
        raise InvalidReasonedAnswer("Model answer did not satisfy the evidence-answer contract.")
    return ValidatedReasonedAnswer(
        answer, tuple(item for item in redacted.items if item.label in used)
    )
