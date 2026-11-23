"""Offline, one-shot causal answer orchestration over a single Module 9 return.

Only a trusted server caller may supply repository inventories, a retriever and
external-processing approval. This module does not load SDKs, files or indexes.
Its output never contains the raw evidence bundle, provenance paths, or keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json

from backend.git_history.models import GitHistoryInventory
from backend.retrieval.provenance import GraphRevision
from backend.retrieval.relevance import RelevancePolicy
from backend.runtime_evidence.models import RuntimeEvidenceInventory

from .answer_models import AnswerStatus, InvalidReasonedAnswer, ReasonedAnswer
from .citations import ValidatedReasonedAnswer, validate_answer
from .evidence import EvidencePolicy, EvidenceKind, assemble_evidence
from .redaction import RedactionPolicy, UnsafeEvidenceForModel, redact_evidence, _safe_text
from .providers.base import (
    GenerationBoundaryError, ReasoningProvider, prepare_generation_request,
)

_DENIED = "External generation has not been authorized for this request."
_INVALID_REQUEST = "Causal question request is invalid or exceeds its limits."
_INVALID_RESULT = "Retrieved evidence did not satisfy the Module 10 contract."
_PROVIDER_FAILURE = "Reasoning provider could not complete this request."
_INVALID_DRAFT = "Reasoning provider returned an invalid or unsupported answer."
_WARNINGS = {
    "history_incomplete": "Git history evidence is incomplete or unverified.",
    "runtime_incomplete": "Runtime evidence is incomplete or unverified.",
    "partial_evidence": "Evidence was redacted or omitted under privacy and selection limits.",
}


class CausalRequestError(ValueError):
    """Invalid caller settings or missing external processing authorization."""


class CausalEvidenceError(ValueError):
    """A Module 9 return is structurally invalid for the selected request."""


class CausalProviderError(RuntimeError):
    """Provider failed; raw provider details must not escape."""


class CausalDraftError(ValueError):
    """Provider returned a structurally invalid or uncitable draft."""


@dataclass(frozen=True, slots=True)
class CausalResult:
    """Safe, citation-validated answer; never retain unredacted evidence."""

    validated: ValidatedReasonedAnswer = field(repr=False)
    warnings: tuple[str, ...] = ()

    @property
    def answer(self) -> ReasonedAnswer:
        return self.validated.answer

    @property
    def citations(self):
        return self.validated.citations


def _encode_candidate(answer: ReasonedAnswer) -> str:
    """Treat a provider's typed draft as untrusted, reparse strict JSON schema."""
    if type(answer) is not ReasonedAnswer:
        raise CausalDraftError(_INVALID_DRAFT)
    try:
        obj = {
            "status": answer.status.value,
            "overview": answer.overview,
            "claims": [{
                "text": claim.text,
                "category": claim.category.value,
                "citations": list(claim.citations),
                **({"excerpt": claim.excerpt} if claim.excerpt is not None else {}),
            } for claim in answer.claims],
            "hypotheses": [{
                "summary": hyp.summary,
                "supporting_citations": list(hyp.supporting_citations),
                "contradicting_citations": list(hyp.contradicting_citations),
                "missing_observations": list(hyp.missing_observations),
                "status": hyp.status.value,
            } for hyp in answer.hypotheses],
            "limitations": list(answer.limitations),
        }
        value = json.dumps(obj, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(value.encode("utf-8")) > 32_768:
            raise CausalDraftError(_INVALID_DRAFT)
        return value
    except (AttributeError, TypeError, ValueError, UnicodeError, RecursionError):
        raise CausalDraftError(_INVALID_DRAFT) from None


def _warnings(redacted) -> tuple[str, ...]:
    warnings = []
    if redacted.history_is_complete is False:
        warnings.append(_WARNINGS["history_incomplete"])
    if redacted.runtime_ingestion_complete is False:
        warnings.append(_WARNINGS["runtime_incomplete"])
    if redacted.omissions:
        warnings.append(_WARNINGS["partial_evidence"])
    return tuple(warnings)


def _retrieve_once(
    retriever, namespace: str, query: str, top_k: int,
    *, graph_revision, git_history, runtime_evidence, relevance_policy,
    graph_top_k: int, history_top_k: int, runtime_top_k: int,
):
    """Dispatch to exactly one public Module 9 method, never re-query."""
    if runtime_evidence is not None:
        return retriever.search_with_runtime(
            namespace, query, top_k, runtime_evidence,
            runtime_top_k=runtime_top_k, relevance_policy=relevance_policy,
            graph_revision=graph_revision, graph_top_k=graph_top_k,
            git_history=git_history, history_top_k=history_top_k,
        )
    if git_history is not None:
        return retriever.search_with_history(
            namespace, query, top_k, git_history,
            history_top_k=history_top_k, relevance_policy=relevance_policy,
            graph_revision=graph_revision, graph_top_k=graph_top_k,
        )
    if graph_revision is not None:
        if relevance_policy is not None:
            return retriever.search_with_graph_relevant(
                namespace, query, top_k, graph_revision, relevance_policy,
                graph_top_k=graph_top_k,
            )
        return retriever.search_with_graph(
            namespace, query, top_k, graph_revision, graph_top_k=graph_top_k,
        )
    if relevance_policy is not None:
        return retriever.search_relevant(namespace, query, top_k, relevance_policy)
    return retriever.search(namespace, query, top_k)


def answer_question(
    retriever,
    provider: ReasoningProvider,
    repository_namespace: str,
    query_text: str,
    *,
    top_k: int = 8,
    graph_revision: GraphRevision | None = None,
    git_history: GitHistoryInventory | None = None,
    runtime_evidence: RuntimeEvidenceInventory | None = None,
    relevance_policy: RelevancePolicy | None = None,
    graph_top_k: int = 8,
    history_top_k: int = 5,
    runtime_top_k: int = 5,
    evidence_policy: EvidencePolicy | None = None,
    redaction_policy: RedactionPolicy | None = None,
    external_processing_approved: bool = False,
) -> CausalResult:
    """Authorize, retrieve once, redact, generate once, and validate citations.

    All inventories and approval MUST come from an authorized server-side
    repository session. This is not a public HTTP/API trust boundary. The
    provider may be a local fake in tests; no adapter is created here.

    Failed Module 9 provenance checks deliberately propagate unchanged.
    Unverified history/runtime clues can never upgrade a citation's trust.
    """
    if (
        type(external_processing_approved) is not bool
        or type(repository_namespace) is not str or not repository_namespace
        or type(query_text) is not str or not query_text.strip()
        or type(top_k) is not int or not 1 <= top_k <= 8
        or type(graph_top_k) is not int or not 1 <= graph_top_k <= 100
        or type(history_top_k) is not int or not 1 <= history_top_k <= 20
        or type(runtime_top_k) is not int or not 1 <= runtime_top_k <= 20
        or (graph_revision is not None and type(graph_revision) is not GraphRevision)
        or (git_history is not None and type(git_history) is not GitHistoryInventory)
        or (runtime_evidence is not None and type(runtime_evidence) is not RuntimeEvidenceInventory)
        or (relevance_policy is not None and type(relevance_policy) is not RelevancePolicy)
        or (evidence_policy is not None and type(evidence_policy) is not EvidencePolicy)
        or (redaction_policy is not None and type(redaction_policy) is not RedactionPolicy)
        or not isinstance(provider, ReasoningProvider)
    ):
        raise CausalRequestError(_INVALID_REQUEST)
    try:
        if (len(query_text) > 4_096 or len(repository_namespace.encode("utf-8")) > 256
            or "\x00" in repository_namespace or "\x00" in query_text):
            raise CausalRequestError(_INVALID_REQUEST)
        _safe_text(query_text, is_query=True)
    except (UnsafeEvidenceForModel, UnicodeError):
        raise CausalRequestError(_INVALID_REQUEST) from None
    if not external_processing_approved:
        # Do not even retrieve code when external generation was not approved.
        raise CausalRequestError(_DENIED)

    # Import result shapes lazily: importing the pure Module 10 package must
    # not eagerly load Module 5 embedding SDKs or create a provider.
    from backend.retrieval.orchestrator import (
        GraphRetrievalResult, HistoricalRetrievalResult, RuntimeRetrievalResult,
    )
    result = _retrieve_once(
        retriever, repository_namespace, query_text, top_k,
        graph_revision=graph_revision, git_history=git_history,
        runtime_evidence=runtime_evidence, relevance_policy=relevance_policy,
        graph_top_k=graph_top_k, history_top_k=history_top_k,
        runtime_top_k=runtime_top_k,
    )
    if type(result) is tuple:
        hits, graph_context, history, runtime = result, (), None, None
    elif type(result) is GraphRetrievalResult:
        hits, graph_context, history, runtime = result.hits, result.context, None, None
    elif type(result) is HistoricalRetrievalResult:
        hits, graph_context, history, runtime = result.hits, result.graph_context, result.history, None
    elif type(result) is RuntimeRetrievalResult:
        hits, graph_context, history, runtime = (
            result.hits, result.graph_context, result.history, result.runtime,
        )
    else:
        raise CausalEvidenceError(_INVALID_RESULT)
    # A mocked/buggy collaborator must not inject extra data by returning a
    # result shape inconsistent with the requested retrieval path.
    if (
        (runtime_evidence is not None) != (type(result) is RuntimeRetrievalResult)
        or (runtime_evidence is None and git_history is not None
            and type(result) is not HistoricalRetrievalResult)
        or (runtime_evidence is None and git_history is None and graph_revision is not None
            and type(result) is not GraphRetrievalResult)
        or (runtime_evidence is None and git_history is None and graph_revision is None
            and type(result) is not tuple)
    ):
        raise CausalEvidenceError(_INVALID_RESULT)
    raw = assemble_evidence(
        repository_namespace, query_text, hits,
        graph_context=graph_context, history=history, runtime=runtime,
        policy=evidence_policy,
    )
    prepared = redact_evidence(raw, policy=redaction_policy)
    warnings = _warnings(prepared)
    if not any(item.kind is EvidenceKind.INDEXED_SOURCE for item in prepared.items):
        # Abstention is deterministic and does not spend or disclose model data.
        abstention = ReasonedAnswer(
            status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            overview="No admissible indexed source evidence is available for this question.",
            claims=(), hypotheses=(),
            limitations=("No source-backed conclusion can be established.",),
        )
        return CausalResult(ValidatedReasonedAnswer(abstention, ()), warnings)
    request = prepare_generation_request(
        raw, prepared, policy=redaction_policy,
        external_processing_approved=external_processing_approved,
    )
    try:
        candidate = provider.generate(request)
    except Exception:
        # Never leak SDK exceptions, prompt bodies or provider diagnostics.
        raise CausalProviderError(_PROVIDER_FAILURE) from None
    try:
        validated = validate_answer(
            raw, prepared, _encode_candidate(candidate), redaction_policy=redaction_policy,
        )
    except (InvalidReasonedAnswer, CausalDraftError):
        raise CausalDraftError(_INVALID_DRAFT) from None
    return CausalResult(validated, warnings)
