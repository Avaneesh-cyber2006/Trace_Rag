"""Strict, provider-neutral *schema* for candidate reasoned answers.

A structurally valid answer is not proof of factual or causal correctness. See
``citations.validate_answer`` for registry checks. No generation SDK imported.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .redaction import _safe_text, UnsafeEvidenceForModel
import json
import re


_INVALID = "Model answer did not satisfy the evidence-answer contract."
_LABEL = re.compile(r"(?:S|G|H|R)[1-9][0-9]*\Z")
_BAD_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HTML = re.compile(r"(?i)<\s*/?\s*(?:script|iframe|object|embed|img|svg|style|link|meta)\b")
_MAX_OUTPUT_BYTES = 32_768


class InvalidReasonedAnswer(ValueError):
    """Answer violates the strict schema, bounds, or citation contract."""

    def __init__(self, message: str, *, reason: str = "ANSWER_INVALID", field: str = "answer"):
        super().__init__(message)
        self.reason = reason
        self.field = field


class AnswerStatus(str, Enum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class ClaimCategory(str, Enum):
    INDEXED_FACT = "indexed_fact"
    GRAPH_RELATION = "graph_relation"
    REPORTED_HISTORY = "reported_history"
    REPORTED_RUNTIME = "reported_runtime"
    HYPOTHESIS = "hypothesis"


class HypothesisStatus(str, Enum):
    POSSIBLE = "possible"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class Claim:
    text: str
    category: ClaimCategory
    citations: tuple[str, ...]
    excerpt: str | None = None


@dataclass(frozen=True, slots=True)
class Hypothesis:
    summary: str
    supporting_citations: tuple[str, ...]
    contradicting_citations: tuple[str, ...]
    missing_observations: tuple[str, ...]
    status: HypothesisStatus


@dataclass(frozen=True, slots=True)
class ReasonedAnswer:
    status: AnswerStatus
    overview: str
    claims: tuple[Claim, ...]
    hypotheses: tuple[Hypothesis, ...]
    limitations: tuple[str, ...]


def _object(value: object, keys: set[str], optional: set[str] = frozenset(), *, field="answer") -> dict:
    if type(value) is not dict:
        raise InvalidReasonedAnswer(_INVALID, reason="OBJECT_TYPE", field=field)
    if not keys <= value.keys():
        raise InvalidReasonedAnswer(_INVALID, reason="MISSING_FIELDS", field=field)
    if set(value) - keys - optional:
        raise InvalidReasonedAnswer(_INVALID, reason="EXTRA_FIELDS", field=field)
    return value


def _text(value: object, max_chars: int, *, allow_empty: bool = False, field="answer") -> str:
    if type(value) is not str:
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_TYPE", field=field)
    if len(value) > max_chars:
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_TOO_LONG", field=field)
    if not value.strip() and not allow_empty:
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_EMPTY", field=field)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_ENCODING", field=field) from None
    if _BAD_CHARS.search(value):
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_CONTROL", field=field)
    if _HTML.search(value):
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_UNSAFE_MARKUP", field=field)
    try:
        _, secret_found = _safe_text(value)
        if secret_found:
            raise InvalidReasonedAnswer(_INVALID, reason="TEXT_PRIVACY", field=field)
    except UnsafeEvidenceForModel:
        raise InvalidReasonedAnswer(_INVALID, reason="TEXT_PRIVACY", field=field) from None
    return value


def _labels(value: object, maximum: int, *, required: bool = False, field="answer") -> tuple[str, ...]:
    if type(value) is not list or len(value) > maximum or (required and not value):
        raise InvalidReasonedAnswer(_INVALID, reason="LABEL_COUNT_OR_TYPE", field=field)
    if any(type(label) is not str or _LABEL.fullmatch(label) is None for label in value):
        raise InvalidReasonedAnswer(_INVALID, reason="LABEL_FORMAT", field=field)
    if len(set(value)) != len(value):
        raise InvalidReasonedAnswer(_INVALID, reason="LABEL_DUPLICATE", field=field)
    return tuple(value)


def _texts(value: object, max_items: int, max_chars: int, *, field="answer") -> tuple[str, ...]:
    if type(value) is not list or len(value) > max_items:
        raise InvalidReasonedAnswer(_INVALID, reason="ARRAY_COUNT_OR_TYPE", field=field)
    return tuple(_text(item, max_chars, field=field) for item in value)


def _enum(enum_class: type[Enum], value: object, *, field="answer") -> Enum:
    if type(value) is not str:
        raise InvalidReasonedAnswer(_INVALID, reason="ENUM_INVALID", field=field)
    try:
        return enum_class(value)
    except ValueError:
        raise InvalidReasonedAnswer(_INVALID, reason="ENUM_INVALID", field=field) from None


def _no_duplicates(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise InvalidReasonedAnswer(_INVALID, reason="JSON_DUPLICATE_KEY")
        value[key] = item
    return value


def parse_answer(payload: str | dict) -> ReasonedAnswer:
    """Parse exactly the accepted bounded JSON schema, without coercion.

    This is a pure schema check. Never trust a parsed result until its IDs,
    excerpts and trust categories are checked against an evidence registry.
    """
    if type(payload) is str:
        try:
            if len(payload.encode("utf-8")) > _MAX_OUTPUT_BYTES:
                raise InvalidReasonedAnswer(_INVALID, reason="OUTPUT_TOO_LARGE")
            def reject_constant(_: str) -> None:
                raise InvalidReasonedAnswer(_INVALID, reason="JSON_NONFINITE")
            payload = json.loads(payload, object_pairs_hook=_no_duplicates,
                                 parse_constant=reject_constant)
        except InvalidReasonedAnswer:
            raise
        except (ValueError, UnicodeError, RecursionError):
            raise InvalidReasonedAnswer(_INVALID, reason="JSON_INVALID") from None
    _object(payload, {"status", "overview", "claims", "hypotheses", "limitations"})
    status = _enum(AnswerStatus, payload["status"], field="status")
    overview = _text(payload["overview"], 2_000, field="overview")
    claims_raw = payload["claims"]
    hypotheses_raw = payload["hypotheses"]
    if type(claims_raw) is not list or len(claims_raw) > 6:
        raise InvalidReasonedAnswer(_INVALID, reason="ARRAY_COUNT_OR_TYPE", field="claims")
    if type(hypotheses_raw) is not list or len(hypotheses_raw) > 3:
        raise InvalidReasonedAnswer(_INVALID, reason="ARRAY_COUNT_OR_TYPE", field="hypotheses")
    claims: list[Claim] = []
    for raw in claims_raw:
        _object(raw, {"text", "category", "citations"}, {"excerpt"}, field="claims")
        text = _text(raw["text"], 1_000, field="claims.text")
        category = _enum(ClaimCategory, raw["category"], field="claims.category")
        citations = _labels(raw["citations"], 8, required=True, field="claims.citations")
        excerpt = raw.get("excerpt")
        if excerpt is not None:
            excerpt = _text(excerpt, 500, field="claims.excerpt")
        claims.append(Claim(text, category, citations, excerpt))
    hypotheses: list[Hypothesis] = []
    for raw in hypotheses_raw:
        _object(raw, {"summary", "supporting_citations", "contradicting_citations",
                      "missing_observations", "status"}, field="hypotheses")
        summary = _text(raw["summary"], 1_000, field="hypotheses.summary")
        support = _labels(raw["supporting_citations"], 8, field="hypotheses.supporting_citations")
        oppose = _labels(raw["contradicting_citations"], 8, field="hypotheses.contradicting_citations")
        missing = _texts(raw["missing_observations"], 8, 300, field="hypotheses.missing_observations")
        if set(support) & set(oppose):
            raise InvalidReasonedAnswer(_INVALID, reason="HYPOTHESIS_LABEL_OVERLAP", field="hypotheses")
        hstatus = _enum(HypothesisStatus, raw["status"], field="hypotheses.status")
        hypotheses.append(Hypothesis(summary, support, oppose, missing, hstatus))
    limitations = _texts(payload["limitations"], 8, 500, field="limitations")
    if status is AnswerStatus.ANSWERED and not (claims or hypotheses):
        raise InvalidReasonedAnswer(_INVALID, reason="ANSWER_WITHOUT_CLAIMS", field="status")
    if status is AnswerStatus.INSUFFICIENT_EVIDENCE and any(
        item.status is HypothesisStatus.POSSIBLE for item in hypotheses
    ):
        raise InvalidReasonedAnswer(_INVALID, reason="STATUS_HYPOTHESIS_CONFLICT", field="status")
    return ReasonedAnswer(status, overview, tuple(claims), tuple(hypotheses), limitations)
