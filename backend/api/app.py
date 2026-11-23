"""Explicit, local-session FastAPI edge for the validated Module 10 answer flow.

No repository indexing, SDK setup or outbound traffic occurs at app creation.
The caller must register already-authorized in-process repository sessions;
this API cannot register or locate repositories using browser-supplied paths.
NOT suitable for public or multi-tenant deployment without session identity,
per-user authorization, request lifecycle and infrastructure controls.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from hmac import compare_digest
import json
import re
from threading import Lock
from time import monotonic
from typing import Callable, Sequence

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_LOOPBACK_ORIGINS = frozenset((
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:3000", "http://127.0.0.1:3000",
))
_ERROR_DETAILS = {
    401: "unauthorized", 403: "forbidden", 404: "repository_not_found",
    409: "evidence_unavailable", 413: "request_too_large",
    415: "unsupported_media_type", 422: "invalid_request",
    429: "rate_limited", 502: "generation_unavailable",
    500: "internal_error",
}


@dataclass(frozen=True, slots=True)
class ApiSecurity:
    """Explicit, never logged session bearer token; default model calls disabled."""

    bearer_token: str = field(repr=False)
    allow_external_processing: bool = False
    allowed_origins: tuple[str, ...] = ()
    max_body_bytes: int = 12_288
    max_requests_per_minute: int = 30

    def __post_init__(self) -> None:
        if (
            type(self.bearer_token) is not str
            or not 24 <= len(self.bearer_token) <= 512
            or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in self.bearer_token)
            or type(self.allow_external_processing) is not bool
            or type(self.allowed_origins) is not tuple
            or any(type(value) is not str or value not in _LOOPBACK_ORIGINS
                   for value in self.allowed_origins)
            or len(set(self.allowed_origins)) != len(self.allowed_origins)
            or type(self.max_body_bytes) is not int
            or not 1_024 <= self.max_body_bytes <= 65_536
            or type(self.max_requests_per_minute) is not int
            or not 1 <= self.max_requests_per_minute <= 120
        ):
            raise ValueError("Invalid local API security configuration.")


@dataclass(frozen=True, slots=True)
class RepositorySession:
    """Trusted server-supplied binding, never construct from HTTP request data.

    The app's bearer token is scoped to the entire registered local session.
    A multi-user app must replace this with user/tenant-specific permissions.
    """

    repository_id: str
    display_name: str
    repository_namespace: str = field(repr=False)
    retriever: object = field(repr=False)
    provider: object = field(repr=False)
    external_processing_permitted: bool = False
    graph_revision: object | None = field(default=None, repr=False)
    git_history: object | None = field(default=None, repr=False)
    runtime_evidence: object | None = field(default=None, repr=False)
    relevance_policy: object | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.repository_id) is not str or _ID.fullmatch(self.repository_id) is None
            or type(self.display_name) is not str
            or not 1 <= len(self.display_name.strip()) <= 128
            or any(ord(c) < 32 or ord(c) == 127 for c in self.display_name)
            or type(self.repository_namespace) is not str
            or not 1 <= len(self.repository_namespace.encode("utf-8")) <= 256
            or any(ord(c) < 32 or ord(c) == 127 for c in self.repository_namespace)
            or not callable(getattr(self.retriever, "search", None))
            or not callable(getattr(self.provider, "generate", None))
            or type(self.external_processing_permitted) is not bool
        ):
            raise ValueError("Invalid registered repository session.")


class AnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    repository_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    question: str = Field(min_length=1, max_length=4_096)
    top_k: int = Field(default=8, ge=1, le=8)
    approve_external_processing: bool = False

    @field_validator("question")
    @classmethod
    def valid_question(cls, value: str) -> str:
        try:
            encoded_size = len(value.encode("utf-8"))
        except UnicodeError:
            raise ValueError("Invalid question.") from None
        if not value.strip() or "\x00" in value or encoded_size > 4_096:
            raise ValueError("Invalid question.")
        return value


class ClaimOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str
    category: str
    citations: list[str]
    excerpt: str | None


class HypothesisOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str
    supporting_citations: list[str]
    contradicting_citations: list[str]
    missing_observations: list[str]
    status: str


class CitationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str
    kind: str
    content: str
    source_label: str | None
    details: dict[str, str]
    redacted: bool


class AnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: str
    overview: str
    claims: list[ClaimOutput]
    hypotheses: list[HypothesisOutput]
    limitations: list[str]


class AnswerEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    repository_id: str
    answer: AnswerOutput
    citations: list[CitationOutput]
    warnings: list[str]


def _error(status: int) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": _ERROR_DETAILS[status]})


class _RequestWindow:
    """Per-instance local request throttle; not a distributed rate limiter."""

    def __init__(self, maximum: int, clock: Callable[[], float]):
        self._maximum = maximum
        self._clock = clock
        self._lock = Lock()
        self._times: deque[float] = deque()

    def admit(self) -> bool:
        with self._lock:
            now = self._clock()
            while self._times and now - self._times[0] >= 60:
                self._times.popleft()
            if len(self._times) >= self._maximum:
                return False
            self._times.append(now)
            return True


def _serialize(repository_id: str, result: object) -> AnswerEnvelope:
    """Copy *only* the citation-validated model and its redacted evidence."""
    answer = result.answer
    return AnswerEnvelope(
        repository_id=repository_id,
        answer=AnswerOutput(
            status=answer.status.value,
            overview=answer.overview,
            claims=[ClaimOutput(
                text=claim.text, category=claim.category.value,
                citations=list(claim.citations), excerpt=claim.excerpt,
            ) for claim in answer.claims],
            hypotheses=[HypothesisOutput(
                summary=hyp.summary,
                supporting_citations=list(hyp.supporting_citations),
                contradicting_citations=list(hyp.contradicting_citations),
                missing_observations=list(hyp.missing_observations),
                status=hyp.status.value,
            ) for hyp in answer.hypotheses],
            limitations=list(answer.limitations),
        ),
        citations=[CitationOutput(
            label=item.label, kind=item.kind.value,
            content=item.content, source_label=item.source_label,
            details=dict(item.details), redacted=item.redacted,
        ) for item in result.citations],
        warnings=list(result.warnings),
    )


def _strict_json_pairs(pairs: list[tuple[str, object]]) -> dict:
    obj: dict = {}
    for name, value in pairs:
        if name in obj:
            raise ValueError("Duplicate request field.")
        obj[name] = value
    return obj


def create_app(
    *,
    security: ApiSecurity,
    repositories: Sequence[RepositorySession],
    answer_service: Callable[..., object] | None = None,
    clock: Callable[[], float] = monotonic,
) -> FastAPI:
    """Construct a *local-only* API with explicit authorization dependencies.

    `answer_service` is test/server injection; it is never browser-provided.
    No global app, filesystem lookup, automatic provider instantiation or
    credential lookup is offered. Bind only to 127.0.0.1 for local testing.
    """
    if (type(security) is not ApiSecurity or not isinstance(repositories, (tuple, list))
        or not callable(clock) or (answer_service is not None and not callable(answer_service))):
        raise ValueError("Invalid local API configuration.")
    registered: dict[str, RepositorySession] = {}
    for binding in repositories:
        if type(binding) is not RepositorySession or binding.repository_id in registered:
            raise ValueError("Duplicate or invalid repository session.")
        registered[binding.repository_id] = binding
    throttle = _RequestWindow(security.max_requests_per_minute, clock)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    if security.allowed_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(security.allowed_origins),
            allow_credentials=False, allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def never_cache(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authorize(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin is not None and origin not in security.allowed_origins:
            raise _error(403)
        header = request.headers.get("authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            raise _error(401)
        candidate = header[len(prefix):]
        if not candidate or not compare_digest(candidate, security.bearer_token):
            raise _error(401)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/repositories")
    def list_repositories(request: Request):
        authorize(request)
        return {"repositories": [
            {"repository_id": binding.repository_id, "display_name": binding.display_name}
            for binding in registered.values()
        ]}

    @app.post("/api/v1/answer")
    async def answer(request: Request):
        authorize(request)
        if not throttle.admit():
            raise _error(429)
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise _error(415)
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > security.max_body_bytes or int(content_length) < 0:
                    raise _error(413)
            except ValueError:
                raise _error(422) from None
        payload = bytearray()
        async for chunk in request.stream():
            if len(payload) + len(chunk) > security.max_body_bytes:
                raise _error(413)
            payload.extend(chunk)
        try:
            def reject_constant(_: str):
                raise ValueError("Invalid JSON constant.")
            parsed = json.loads(bytes(payload), object_pairs_hook=_strict_json_pairs,
                                parse_constant=reject_constant)
            input_model = AnswerInput.model_validate(parsed)
        except (ValueError, UnicodeError, ValidationError, RecursionError):
            raise _error(422) from None
        binding = registered.get(input_model.repository_id)
        if binding is None:
            raise _error(404)
        if not (
            input_model.approve_external_processing
            and security.allow_external_processing
            and binding.external_processing_permitted
        ):
            raise _error(403)
        try:
            service = answer_service
            if service is None:
                # Late import preserves API/module boundary when no repository is active.
                from backend.causal_reasoner.causal import answer_question
                service = answer_question
            result = await run_in_threadpool(
                service, binding.retriever, binding.provider,
                binding.repository_namespace, input_model.question,
                top_k=input_model.top_k,
                graph_revision=binding.graph_revision,
                git_history=binding.git_history,
                runtime_evidence=binding.runtime_evidence,
                relevance_policy=binding.relevance_policy,
                external_processing_approved=True,
            )
            response = _serialize(input_model.repository_id, result)
        except Exception as exc:
            # Fail closed with content-free errors; never include raw exception text.
            from backend.causal_reasoner.causal import (
                CausalRequestError, CausalEvidenceError, CausalProviderError, CausalDraftError,
            )
            from backend.causal_reasoner.evidence import InvalidEvidenceBundle
            from backend.causal_reasoner.redaction import UnsafeEvidenceForModel
            from backend.retrieval.errors import RetrievalError
            if isinstance(exc, CausalRequestError):
                raise _error(422) from None
            if isinstance(exc, (CausalProviderError, CausalDraftError)):
                raise _error(502) from None
            if isinstance(exc, (CausalEvidenceError, InvalidEvidenceBundle,
                                UnsafeEvidenceForModel, RetrievalError)):
                raise _error(409) from None
            raise _error(500) from None
        return JSONResponse(response.model_dump(mode="json"))

    return app
