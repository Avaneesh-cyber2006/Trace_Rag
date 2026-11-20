import ast
import hashlib
import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import pytest

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence import (
    RuntimeArtifact, RuntimeEvidenceIngestor, RuntimeEvidenceRequest,
)
from backend.runtime_evidence.exceptions import RuntimeEvidenceInvariantError
from backend.runtime_evidence.identities import (
    artifact_id, event_fingerprint, event_id, frame_id,
    source_reference_sha256,
)
from backend.runtime_evidence.models import (
    FrameAssociation, PathMatchBasis, RuntimeArtifactFormat, RuntimeEventKind,
    RuntimeFrameLanguage, RuntimeStackFrame, RuntimeTag,
)


CONTENT_SHA = hashlib.sha256(b"hello\n").hexdigest()
ARTIFACT = "09f8483cbc155fd3e52bf5d82de4af300c99ffac76212b1f2f73f739c7f89993"
EVENT = "c34329c46e8733344d378749ce8f3244b367a9be403f4b8b1b8f321eabbf93ec"
FRAME = "8abaeb036ef874fb9c03e109cc7883ddf0ec09d9cb6fc846448d27f264a4010d"
SOURCE_HASH = (
    "815183491aa22012907fbd76531faaeaab8c5b829f7206b49c7838928994a0a9")
INVALID_MESSAGE = "invalid runtime evidence identity material"
HUGE_INT = 10 ** 5000
IDENTITIES_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "backend" / "runtime_evidence" / "identities.py"
)

FINGERPRINT_OPTIONAL_FIELDS = (
    "timestamp_text",
    "level",
    "message",
    "logger",
    "operation",
    "trace_id",
    "span_id",
    "parent_span_id",
    "duration_ms_text",
    "status",
    "error_type",
    "error_message",
)


class _TupleSubclass(tuple):
    pass


def _canonical_sha256(payload):
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _frame(ordinal, **overrides):
    values = {
        "frame_id": ("1" if ordinal == 0 else "2") * 64,
        "event_id": "c" * 64,
        "ordinal": ordinal,
        "language": RuntimeFrameLanguage.UNKNOWN,
        "function_text": None,
        "relative_path": None,
        "line": None,
        "column": None,
        "source_reference_sha256": None,
        "path_match_basis": PathMatchBasis.UNMATCHED,
        "association": FrameAssociation.UNASSOCIATED,
        "primary_node_id": None,
        "candidate_node_ids": (),
    }
    values.update(overrides)
    return RuntimeStackFrame(**values)


def _invalid_frame(**overrides):
    frame = _frame(0)
    for name, value in overrides.items():
        object.__setattr__(frame, name, value)
    return frame


def _invalid_tag(key, value):
    tag = object.__new__(RuntimeTag)
    object.__setattr__(tag, "key", key)
    object.__setattr__(tag, "value", value)
    return tag


def _fingerprint_kwargs(**overrides):
    values = {
        "repository_namespace": "repo",
        "kind": RuntimeEventKind.LOG,
        "timestamp_text": None,
        "level": None,
        "message": None,
        "logger": None,
        "operation": None,
        "trace_id": None,
        "span_id": None,
        "parent_span_id": None,
        "duration_ms_text": None,
        "status": None,
        "error_type": None,
        "error_message": None,
        "tags": (),
        "frames": (),
    }
    values.update(overrides)
    return values


def _fingerprint(tags=(), frames=(), **overrides):
    return event_fingerprint(
        **_fingerprint_kwargs(tags=tags, frames=frames, **overrides)
    )


def test_pinned_artifact_event_and_frame_ids():
    assert artifact_id(
        "repo", RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA
    ) == ARTIFACT
    assert event_id("repo", ARTIFACT, 0) == EVENT
    assert frame_id("repo", EVENT, 0) == FRAME


def test_pinned_source_reference_hash_and_fingerprint():
    assert source_reference_sha256("pkg/app.py") == (
        "815183491aa22012907fbd76531faaeaab8c5b829f7206b49c7838928994a0a9")
    assert event_fingerprint(
        repository_namespace="repo", kind=RuntimeEventKind.LOG,
        timestamp_text=None, level="INFO", message="hello", logger=None,
        operation=None, trace_id=None, span_id=None, parent_span_id=None,
        duration_ms_text=None, status=None, error_type=None,
        error_message=None, tags=(), frames=()) == (
        "af9aeed9c7f817b89c5caa7a062d591875a091907b93fa8aa0ab8b02d024ef82")


def test_artifact_payload_is_exact_canonical_array():
    payload = [
        "tracerag-runtime-artifact-v1", "repo", "text_log", None,
        CONTENT_SHA,
    ]
    assert artifact_id(
        "repo", RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA
    ) == _canonical_sha256(payload)


def test_artifact_payload_includes_declared_language():
    payload = [
        "tracerag-runtime-artifact-v1", "repo", "stack_trace_text",
        "python", CONTENT_SHA,
    ]
    assert artifact_id(
        "repo", RuntimeArtifactFormat.STACK_TRACE_TEXT,
        RuntimeFrameLanguage.PYTHON, CONTENT_SHA,
    ) == _canonical_sha256(payload)


def test_event_payload_is_exact_canonical_array():
    payload = ["tracerag-runtime-event-v1", "repo", ARTIFACT, 0]
    assert event_id("repo", ARTIFACT, 0) == _canonical_sha256(payload)


def test_frame_payload_is_exact_canonical_array():
    payload = ["tracerag-runtime-frame-v1", "repo", EVENT, 0]
    assert frame_id("repo", EVENT, 0) == _canonical_sha256(payload)


def test_source_reference_payload_is_exact_canonical_array():
    payload = ["tracerag-runtime-source-reference-v1", "pkg/app.py"]
    assert source_reference_sha256("pkg/app.py") == _canonical_sha256(
        payload
    )


def test_fingerprint_payload_is_exact_canonical_array():
    first = _frame(
        0,
        language=RuntimeFrameLanguage.PYTHON,
        function_text="main",
        relative_path="pkg/app.py",
        line=10,
        column=4,
        source_reference_sha256=SOURCE_HASH,
        path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
        association=FrameAssociation.FILE,
        primary_node_id="node:app",
        candidate_node_ids=("node:app",),
    )
    second = _frame(1, language=RuntimeFrameLanguage.JAVA)
    payload = [
        "tracerag-runtime-event-fingerprint-v1",
        "repo",
        "error",
        "2026-01-01T00:00:00Z",
        "ERROR",
        "boom",
        "pkg.app",
        "request",
        "trace-1",
        "span-1",
        "parent-1",
        "12.5",
        "500",
        "ValueError",
        "bad",
        [["a", "first"], ["z", "last"]],
        [
            [0, "python", "main", "pkg/app.py", 10, 4, SOURCE_HASH],
            [1, "java", None, None, None, None, None],
        ],
    ]
    assert event_fingerprint(
        repository_namespace="repo",
        kind=RuntimeEventKind.ERROR,
        timestamp_text="2026-01-01T00:00:00Z",
        level="ERROR",
        message="boom",
        logger="pkg.app",
        operation="request",
        trace_id="trace-1",
        span_id="span-1",
        parent_span_id="parent-1",
        duration_ms_text="12.5",
        status="500",
        error_type="ValueError",
        error_message="bad",
        tags=(RuntimeTag("z", "last"), RuntimeTag("a", "first")),
        frames=(second, first),
    ) == _canonical_sha256(payload)


def test_unicode_is_preserved_and_lone_surrogates_fail_without_encoding_error():
    assert source_reference_sha256("pkg/é😀.py") == _canonical_sha256(
        ["tracerag-runtime-source-reference-v1", "pkg/é😀.py"]
    )
    with pytest.raises(RuntimeEvidenceInvariantError) as exc_info:
        source_reference_sha256("pkg/\ud800.py")
    assert str(exc_info.value) == INVALID_MESSAGE
    assert exc_info.value.__cause__ is None


def test_fingerprint_sorts_tags_and_frames_without_changing_ids():
    tags_forward = (RuntimeTag("a", "1"), RuntimeTag("b", "2"))
    tags_reverse = tuple(reversed(tags_forward))
    frames_forward = (_frame(0), _frame(1))
    frames_reverse = tuple(reversed(frames_forward))

    assert _fingerprint(tags_forward, frames_forward) == _fingerprint(
        tags_reverse, frames_reverse)
    assert tags_forward == (RuntimeTag("a", "1"), RuntimeTag("b", "2"))
    assert frames_forward == (_frame(0), _frame(1))
    assert artifact_id("repo", RuntimeArtifactFormat.TEXT_LOG, None,
                       CONTENT_SHA) == ARTIFACT
    assert event_id("repo", ARTIFACT, 0) == EVENT
    assert frame_id("repo", EVENT, 0) == FRAME


def test_timestamp_text_changes_fingerprint_but_not_ids():
    first = _fingerprint(timestamp_text="2026-01-01T00:00:00Z")
    second = _fingerprint(timestamp_text="2026-01-01T00:00:01Z")
    assert first != second
    assert artifact_id(
        "repo", RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA
    ) == ARTIFACT
    assert event_id("repo", ARTIFACT, 0) == EVENT
    assert frame_id("repo", EVENT, 0) == FRAME


def test_timestamp_bytes_inside_artifact_only_change_content_addressing():
    first_content = b"2026-01-01T00:00:00Z hello\n"
    second_content = b"2026-01-01T00:00:01Z hello\n"
    first_sha = hashlib.sha256(first_content).hexdigest()
    second_sha = hashlib.sha256(second_content).hexdigest()
    assert first_sha != second_sha
    first_artifact = artifact_id(
        "repo", RuntimeArtifactFormat.TEXT_LOG, None, first_sha
    )
    second_artifact = artifact_id(
        "repo", RuntimeArtifactFormat.TEXT_LOG, None, second_sha
    )
    assert first_artifact != second_artifact
    assert event_id("repo", ARTIFACT, 0) == EVENT
    assert frame_id("repo", EVENT, 0) == FRAME


def test_ids_are_graph_independent_across_calls():
    artifact_ids = {
        artifact_id("repo", RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA)
        for _ in range(5)
    }
    event_ids = {event_id("repo", ARTIFACT, 0) for _ in range(5)}
    frame_ids = {frame_id("repo", EVENT, 0) for _ in range(5)}
    source_hashes = {
        source_reference_sha256("pkg/app.py") for _ in range(5)
    }
    fingerprints = {
        _fingerprint(level="INFO", message="hello") for _ in range(5)
    }
    assert artifact_ids == {ARTIFACT}
    assert event_ids == {EVENT}
    assert frame_ids == {FRAME}
    assert source_hashes == {SOURCE_HASH}
    assert fingerprints == {
        "af9aeed9c7f817b89c5caa7a062d591875a091907b93fa8aa0ab8b02d024ef82"
    }


def test_null_optionals_serialize_as_json_null():
    payload = [
        "tracerag-runtime-event-fingerprint-v1",
        "repo",
        "log",
        *([None] * 12),
        [],
        [],
    ]
    assert _fingerprint() == _canonical_sha256(payload)


def test_event_ids_distinguish_adjacent_sequences():
    assert event_id("repo", ARTIFACT, 0) != event_id("repo", ARTIFACT, 1)
    assert event_id("repo", ARTIFACT, 0) != event_id("repo", ARTIFACT, 2)


def test_frame_ids_distinguish_adjacent_ordinals():
    assert frame_id("repo", EVENT, 0) != frame_id("repo", EVENT, 1)
    assert frame_id("repo", EVENT, 0) != frame_id("repo", EVENT, 2)


def test_fingerprint_includes_repository_namespace():
    assert _fingerprint(repository_namespace="repo") != _fingerprint(
        repository_namespace="other-repo"
    )


def test_fingerprint_excludes_frame_identity_and_path_metadata():
    unassociated = _frame(0)
    not_evaluated = _frame(
        0,
        frame_id="3" * 64,
        event_id="d" * 64,
        path_match_basis=PathMatchBasis.EXTERNAL_OR_NON_FILE,
        association=FrameAssociation.NOT_EVALUATED,
    )
    assert _fingerprint(frames=(unassociated,)) == _fingerprint(
        frames=(not_evaluated,)
    )

    file_frame = _frame(
        0,
        relative_path="pkg/app.py",
        source_reference_sha256=SOURCE_HASH,
        path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
        association=FrameAssociation.FILE,
        primary_node_id="node:a",
        candidate_node_ids=("node:a",),
    )
    symbol_frame = _frame(
        0,
        frame_id="4" * 64,
        event_id="e" * 64,
        relative_path="pkg/app.py",
        source_reference_sha256=SOURCE_HASH,
        path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
        association=FrameAssociation.SYMBOL,
        primary_node_id="node:a",
        candidate_node_ids=("node:a", "node:b"),
    )
    other_candidate = _frame(
        0,
        frame_id="5" * 64,
        event_id="f" * 64,
        relative_path="pkg/app.py",
        source_reference_sha256=SOURCE_HASH,
        path_match_basis=PathMatchBasis.EXACT_RELATIVE_PATH,
        association=FrameAssociation.FILE,
        primary_node_id="node:z",
        candidate_node_ids=("node:z",),
    )
    baseline = _fingerprint(frames=(file_frame,))
    assert baseline == _fingerprint(frames=(symbol_frame,))
    assert baseline == _fingerprint(frames=(other_candidate,))


def test_public_signatures_are_exact_and_exclude_metadata_inputs():
    def names(function):
        return tuple(inspect.signature(function).parameters)

    assert names(artifact_id) == (
        "repository_namespace", "artifact_format", "declared_language",
        "content_sha256",
    )
    assert names(event_id) == (
        "repository_namespace", "artifact_id", "sequence",
    )
    assert names(frame_id) == (
        "repository_namespace", "event_id", "ordinal",
    )
    assert names(source_reference_sha256) == ("relative_path",)

    fingerprint_names = names(event_fingerprint)
    assert fingerprint_names == (
        "repository_namespace", "kind", "timestamp_text", "level", "message",
        "logger", "operation", "trace_id", "span_id", "parent_span_id",
        "duration_ms_text", "status", "error_type", "error_message", "tags",
        "frames",
    )
    for parameter in inspect.signature(
        event_fingerprint
    ).parameters.values():
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    for excluded in (
        "artifact_id", "sequence", "source_line", "source_name",
        "source_names", "path_match_basis", "association", "primary_node_id",
        "candidate_node_ids", "text_truncated", "frames_truncated", "graph",
    ):
        assert excluded not in fingerprint_names


def test_source_reference_hash_does_not_normalize_path_text():
    for path in ("pkg\\app.py", "pkg/./app.py", "./pkg/app.py"):
        assert source_reference_sha256(path) == _canonical_sha256(
            ["tracerag-runtime-source-reference-v1", path]
        )


def test_identities_module_has_no_hash_or_clock_calls():
    tree = ast.parse(IDENTITIES_SOURCE.read_text(encoding="utf-8"))
    forbidden_call_names = {"hash"}
    forbidden_call_attributes = {
        "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter",
        "perf_counter_ns", "now", "utcnow", "today", "uuid1", "uuid4",
        "urandom",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_call_names
        if isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden_call_attributes


def test_identities_module_has_no_forbidden_imports_or_encoding_options():
    source = IDENTITIES_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_roots = {
        "time", "datetime", "random", "uuid", "os", "pathlib", "logging",
        "secrets", "subprocess", "socket", "urllib", "requests",
    }
    imported_roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(
                alias.name.split(".", 1)[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    assert imported_roots.isdisjoint(forbidden_roots)
    assert "surrogatepass" not in source
    assert "sort_keys" not in source
    assert "unicodedata" not in source
    assert "casefold" not in source
    assert "graph" not in source
    for forbidden_text in (
        "source_name", "source_names", "abspath", "realpath", "expanduser",
        "os.path", "os.sep",
    ):
        assert forbidden_text not in source
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(
            node.value, str
        ):
            continue
        assert not node.value.startswith("/")
        assert not (
            len(node.value) >= 2
            and node.value[0].isalpha()
            and node.value[1] == ":"
        )


@pytest.mark.parametrize(
    ("call", "sentinel"),
    [
        pytest.param(
            lambda: artifact_id(
                7, RuntimeArtifactFormat.TEXT_LOG, None, CONTENT_SHA),
            "7", id="artifact-namespace-type"),
        pytest.param(
            lambda: artifact_id("repo", "text_log", None, CONTENT_SHA),
            "text_log", id="artifact-format-string"),
        pytest.param(
            lambda: artifact_id(
                "repo", RuntimeArtifactFormat.TEXT_LOG, "python", CONTENT_SHA),
            "python", id="artifact-language-string"),
        pytest.param(
            lambda: artifact_id(
                "repo", RuntimeArtifactFormat.TEXT_LOG, None, b"digest"),
            "digest", id="artifact-content-not-text"),
        pytest.param(
            lambda: event_id("repo", ARTIFACT, True),
            "True", id="event-sequence-bool"),
        pytest.param(
            lambda: event_id("repo", ARTIFACT, -1),
            "-1", id="event-sequence-negative"),
        pytest.param(
            lambda: event_id("repo", b"artifact-secret", 0),
            "artifact-secret", id="event-artifact-not-text"),
        pytest.param(
            lambda: frame_id("repo", EVENT, 1.0),
            "1.0", id="frame-ordinal-float"),
        pytest.param(
            lambda: frame_id("repo", EVENT, False),
            "False", id="frame-ordinal-bool"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(repository_namespace="repo\ud800")),
            "\ud800", id="fingerprint-namespace-surrogate"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(kind="log")),
            "log", id="fingerprint-kind-string"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(tags=[])),
            "[]", id="fingerprint-tags-list"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(tags=_TupleSubclass())),
            "_TupleSubclass", id="fingerprint-tags-subclass"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(tags=("tag-secret",))),
            "tag-secret", id="fingerprint-tag-not-record"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    tags=(RuntimeTag("dup-secret", "1"),
                          RuntimeTag("dup-secret", "2"),))),
            "dup-secret", id="fingerprint-duplicate-tag-keys"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    tags=(_invalid_tag("key", 1),))),
            "1", id="fingerprint-tag-value-type"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(frames=[])),
            "[]", id="fingerprint-frames-list"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(frames=_TupleSubclass())),
            "_TupleSubclass", id="fingerprint-frames-subclass"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(frames=("frame-secret",))),
            "frame-secret", id="fingerprint-frame-not-record"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(frames=(_frame(0), _frame(0)))),
            "0", id="fingerprint-duplicate-ordinals"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(_invalid_frame(language="python"),))),
            "python", id="fingerprint-frame-language-string"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(_invalid_frame(ordinal=True),))),
            "True", id="fingerprint-frame-ordinal-bool"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(_invalid_frame(line=1.5),))),
            "1.5", id="fingerprint-frame-line-float"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(_invalid_frame(column=-1),))),
            "-1", id="fingerprint-frame-column-negative"),
    ],
)
def test_invalid_material_raises_fixed_invariant_error(call, sentinel):
    with pytest.raises(RuntimeEvidenceInvariantError) as exc_info:
        call()
    message = str(exc_info.value)
    assert message == INVALID_MESSAGE
    assert sentinel not in message
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(
            lambda: event_id("repo", ARTIFACT, HUGE_INT),
            id="event-sequence-huge-int"),
        pytest.param(
            lambda: frame_id("repo", EVENT, HUGE_INT),
            id="frame-ordinal-huge-int"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(replace(_frame(0), ordinal=HUGE_INT),))),
            id="fingerprint-frame-ordinal-huge-int"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(_frame(0, line=HUGE_INT),))),
            id="fingerprint-frame-line-huge-int"),
        pytest.param(
            lambda: event_fingerprint(
                **_fingerprint_kwargs(
                    frames=(_frame(0, column=HUGE_INT),))),
            id="fingerprint-frame-column-huge-int"),
    ],
)
def test_huge_ints_fail_closed_with_fixed_invariant_error(call):
    with pytest.raises(RuntimeEvidenceInvariantError) as exc_info:
        call()
    message = str(exc_info.value)
    assert message == INVALID_MESSAGE
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("field_name", FINGERPRINT_OPTIONAL_FIELDS)
def test_event_fingerprint_rejects_surrogate_in_optional_text(field_name):
    with pytest.raises(RuntimeEvidenceInvariantError) as exc_info:
        event_fingerprint(
            **_fingerprint_kwargs(**{field_name: "\ud800"})
        )
    message = str(exc_info.value)
    assert message == INVALID_MESSAGE
    assert "\ud800" not in message
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize(
    ("tags", "frames"),
    [
        pytest.param(
            (_invalid_tag("\ud800", "value"),), (), id="tag-key"),
        pytest.param(
            (_invalid_tag("key", "\ud800"),), (), id="tag-value"),
        pytest.param(
            (), (_invalid_frame(function_text="\ud800"),),
            id="frame-function-text"),
        pytest.param(
            (), (_invalid_frame(relative_path="\ud800"),),
            id="frame-relative-path"),
        pytest.param(
            (), (_invalid_frame(source_reference_sha256="\ud800"),),
            id="frame-source-reference-hash"),
    ],
)
def test_event_fingerprint_rejects_surrogate_in_tags_and_frames(
    tags, frames
):
    with pytest.raises(RuntimeEvidenceInvariantError) as exc_info:
        event_fingerprint(**_fingerprint_kwargs(tags=tags, frames=frames))
    message = str(exc_info.value)
    assert message == INVALID_MESSAGE
    assert "\ud800" not in message
    assert exc_info.value.__cause__ is None


def _runtime_request(*artifacts):
    return RuntimeEvidenceRequest("repo", tuple(artifacts))


def _file_node(node_id, relative_path):
    return GraphNode(
        node_id=node_id,
        node_kind=GraphNodeKind.FILE,
        repository_namespace="repo",
        relative_path=relative_path,
        language="python",
        parse_status="success",
        symbol_kind=None,
        name=None,
        qualified_name=None,
        location=None,
    )


def _file_graph(relative_path):
    return CodeGraphInventory(
        "repo",
        (_file_node("node-app", relative_path),),
        (),
        (),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
        MappingProxyType({}),
    )


def _id_tuples(inventory):
    return (
        tuple(artifact.artifact_id for artifact in inventory.artifacts),
        tuple(event.event_id for event in inventory.events),
        tuple(frame.frame_id for frame in inventory.frames),
    )


def test_fingerprint_groups_equal_normalized_content_without_merging_occurrences():
    request = _runtime_request(
        RuntimeArtifact(
            "a.jsonl",
            RuntimeArtifactFormat.JSON_EVENTS,
            b'{"kind":"log","message":"same"}\n',
        ),
        RuntimeArtifact(
            "b.jsonl",
            RuntimeArtifactFormat.JSON_EVENTS,
            b'{ "kind": "log", "message": "same" }\n',
        ),
    )
    inventory = RuntimeEvidenceIngestor().ingest(request)
    assert len(inventory.artifacts) == 2
    assert len(inventory.events) == 2
    assert len({
        artifact.artifact_id for artifact in inventory.artifacts
    }) == 2
    first, second = inventory.events
    assert first.event_id != second.event_id
    assert first.sequence == 0
    assert second.sequence == 0
    assert first.event_fingerprint == second.event_fingerprint
    assert inventory.events_by_fingerprint[
        first.event_fingerprint
    ] == inventory.events
    repeated = RuntimeEvidenceIngestor().ingest(request)
    assert _id_tuples(repeated) == _id_tuples(inventory)


def test_repeated_occurrences_in_one_artifact_keep_sequences_and_ids():
    request = _runtime_request(
        RuntimeArtifact(
            "events.jsonl",
            RuntimeArtifactFormat.JSON_EVENTS,
            b'{"kind":"log","message":"same"}\n'
            b'{"kind":"log","message":"same"}\n',
        )
    )
    inventory = RuntimeEvidenceIngestor().ingest(request)
    first, second = inventory.events
    assert [event.sequence for event in inventory.events] == [0, 1]
    assert first.event_id != second.event_id
    assert first.event_fingerprint == second.event_fingerprint
    assert inventory.events_by_fingerprint[
        first.event_fingerprint
    ] == inventory.events
    repeated = RuntimeEvidenceIngestor().ingest(request)
    assert _id_tuples(repeated) == _id_tuples(inventory)


def test_identity_tuples_are_identical_across_repeated_runs_and_graphs():
    request = _runtime_request(
        RuntimeArtifact("a.log", RuntimeArtifactFormat.TEXT_LOG, b"one\n"),
        RuntimeArtifact(
            "b.jsonl",
            RuntimeArtifactFormat.JSON_EVENTS,
            b'{"kind":"log","message":"two"}\n',
        ),
        RuntimeArtifact(
            "c.txt",
            RuntimeArtifactFormat.STACK_TRACE_TEXT,
            b'  File "pkg/app.py", line 1, in f\n',
            RuntimeFrameLanguage.PYTHON,
        ),
    )
    graph = _file_graph("pkg/app.py")
    without_graph = [
        RuntimeEvidenceIngestor().ingest(request) for _ in range(2)
    ]
    with_graph = [
        RuntimeEvidenceIngestor().ingest(request, graph) for _ in range(2)
    ]
    for pair in (without_graph, with_graph):
        assert pair[0] == pair[1]
        for name in (
            "events_by_artifact",
            "events_by_trace_id",
            "events_by_fingerprint",
            "frames_by_file",
            "frames_by_node",
        ):
            assert getattr(pair[0], name) is not getattr(pair[1], name)
    assert len({
        _id_tuples(inventory)
        for inventory in (*without_graph, *with_graph)
    }) == 1
