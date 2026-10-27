from types import MappingProxyType, SimpleNamespace

import pytest

from backend.code_graph import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.runtime_evidence.config import RuntimeEvidenceConfig
from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceGraphError, RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor
from backend.runtime_evidence.models import (
    CaptureCompleteness, FrameAssociation, GraphBinding, PathMatchBasis,
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeArtifactStatus,
    RuntimeEvidenceRequest, RuntimeFrameLanguage, RuntimeIncompleteReason,
    RuntimeIssueCode,
)
from backend.runtime_evidence.validation import (
    require_relative_posix_path, require_scalar_text, truncate_utf8_text,
    utf8_size,
)


def test_scalar_validation_rejects_every_surrogate_and_nul():
    for bad in ("\ud800", "\udfff", "a\ud800b", "\ud83d\ude00", "a\x00b"):
        with pytest.raises(ValueError):
            require_scalar_text(bad, "field", ValueError)
    assert require_scalar_text("😀", "field", ValueError) == "😀"


def test_scalar_validation_rejects_non_strings():
    for bad in (None, 1, b"x", object()):
        with pytest.raises(ValueError):
            require_scalar_text(bad, "field", ValueError)


def test_multibyte_byte_limits_are_utf8_bytes_not_characters():
    assert utf8_size("é") == 2
    assert require_scalar_text("é" * 128, "namespace", ValueError,
                               max_bytes=256) == "é" * 128
    with pytest.raises(ValueError):
        require_scalar_text("é" * 128 + "a", "namespace", ValueError,
                            max_bytes=256)


@pytest.mark.parametrize("bad", ["", "/a", "C:/a", "C:a", r"a\b",
                                 "a//b", "a/./b", "a/../b", "a/", "a\x00b",
                                 "a\x01b", "a\tb", "a\ud800b"])
def test_relative_posix_path_rejects_unsafe_forms(bad):
    with pytest.raises(ValueError):
        require_relative_posix_path(bad, "path", ValueError)


def test_relative_posix_path_accepts_canonical_components():
    assert require_relative_posix_path("pkg/app.py", "path", ValueError) == "pkg/app.py"


def test_text_truncation_is_codepoint_safe_and_reserves_ellipsis():
    assert truncate_utf8_text("abc", 3) == ("abc", False)
    assert truncate_utf8_text("abc😀", 5) == ("ab…", True)
    assert truncate_utf8_text("😀x", 4) == ("", True)
    assert truncate_utf8_text("x", 1) == ("x", False)


# --- Task 16: ingestion pipeline -------------------------------------------

_STACK_TEXT = (
    b"Traceback (most recent call last):\n"
    b'  File "pkg/a.py", line 10, in f\n'
    b'  File "pkg/b.py", line 20, in g\n'
    b"ValueError: boom\n"
)


def _artifact(source="ci.log", content=b"ok\n", fmt=RuntimeArtifactFormat.TEXT_LOG,
              language=None):
    return RuntimeArtifact(source, fmt, content, language)


def _request(*artifacts, namespace="repo", **claims):
    return RuntimeEvidenceRequest(namespace, tuple(artifacts), **claims)


def _loc(start_line=1, end_line=1, start_column=0, end_column=0):
    return SimpleNamespace(
        start_byte=0, end_byte=10, start_line=start_line,
        start_column=start_column, end_line=end_line, end_column=end_column)


def _node(node_id, kind, path, *, namespace="repo", location=None):
    return GraphNode(
        node_id=node_id, node_kind=kind, repository_namespace=namespace,
        relative_path=path, language="python", parse_status="success",
        symbol_kind=None, name=None, qualified_name=None, location=location)


def _graph(nodes=(), *, namespace="repo"):
    return CodeGraphInventory(
        namespace, tuple(nodes), (), (),
        MappingProxyType({}), MappingProxyType({}),
        MappingProxyType({}), MappingProxyType({}))


def _issues_for(inventory, artifact_id):
    return tuple(
        issue for issue in inventory.issues
        if issue.artifact_id == artifact_id
    )


def test_empty_request_returns_empty_complete_inventory():
    inventory = RuntimeEvidenceIngestor().ingest(_request())
    assert inventory.artifacts == ()
    assert inventory.events == ()
    assert inventory.frames == ()
    assert inventory.issues == ()
    assert inventory.submitted_artifacts == 0
    assert inventory.duplicate_artifacts == 0
    assert inventory.total_input_bytes == 0
    assert inventory.total_events == 0
    assert inventory.total_frames == 0
    assert inventory.text_truncated is False
    assert inventory.events_truncated is False
    assert inventory.frames_truncated is False
    assert inventory.issues_truncated is False
    assert inventory.incomplete_reasons == ()
    assert inventory.ingestion_complete is True
    assert inventory.graph_binding is GraphBinding.NOT_PROVIDED
    assert inventory.event_text_retained is True
    for name in (
        "events_by_artifact", "events_by_trace_id", "events_by_fingerprint",
        "frames_by_file", "frames_by_node",
    ):
        mapping = getattr(inventory, name)
        assert isinstance(mapping, MappingProxyType)
        assert mapping == {}


def test_submitted_artifact_count_envelope_is_exact_and_fatal_one_over():
    config = RuntimeEvidenceConfig(max_artifacts=2)
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("a", b"a"), _artifact("b", b"b")))
    assert inventory.submitted_artifacts == 2
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(config).ingest(_request(
            _artifact("a", b"a"), _artifact("b", b"b"),
            _artifact("c", b"c")))


def test_submitted_byte_envelope_is_exact_and_fatal_one_over():
    config = RuntimeEvidenceConfig(
        max_artifact_bytes=4, max_total_input_bytes=4,
        max_submitted_input_bytes=4)
    inventory = RuntimeEvidenceIngestor(config).ingest(
        _request(_artifact("a", b"abcd")))
    assert inventory.total_input_bytes == 4
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(config).ingest(
            _request(_artifact("a", b"abcde")))


def test_envelopes_precede_hashing(monkeypatch):
    import backend.runtime_evidence.ingestor as ingestor

    def forbidden(*args, **kwargs):
        raise AssertionError("hash was reached")

    monkeypatch.setattr(ingestor.hashlib, "sha256", forbidden)
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(
            RuntimeEvidenceConfig(max_artifacts=1)).ingest(_request(
                _artifact("a", b"a"), _artifact("b", b"b")))
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(
            max_artifact_bytes=3, max_total_input_bytes=3,
            max_submitted_input_bytes=3)).ingest(
                _request(_artifact("a", b"abcd")))


def test_envelope_rejection_precedes_adapter_dispatch(monkeypatch):
    import backend.runtime_evidence.ingestor as ingestor

    calls = []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("adapter was reached")

    monkeypatch.setattr(ingestor, "parse_text_log", spy)
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(
            RuntimeEvidenceConfig(max_artifacts=1)).ingest(_request(
                _artifact("a", b"a"), _artifact("b", b"b")))
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(
            max_artifact_bytes=3, max_total_input_bytes=3,
            max_submitted_input_bytes=3)).ingest(
                _request(_artifact("a", b"abcd")))
    assert calls == []


def test_unique_total_limit_uses_deduplicated_sizes():
    config = RuntimeEvidenceConfig(
        max_artifact_bytes=3, max_total_input_bytes=3,
        max_submitted_input_bytes=10)
    with pytest.raises(RuntimeEvidenceLimitError):
        RuntimeEvidenceIngestor(config).ingest(_request(
            _artifact("a", b"aa"), _artifact("b", b"bb")))
    inventory = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("a", b"aa"), _artifact("a", b"aa")))
    assert inventory.total_input_bytes == 2
    assert inventory.submitted_artifacts == 2
    assert inventory.duplicate_artifacts == 1


@pytest.mark.parametrize("second", [
    _artifact("ci.log", b"two\n"),
    _artifact("ci.log", b"one\n", fmt=RuntimeArtifactFormat.JSON_EVENTS),
    _artifact("ci.log", b'{"kind":"log","message":"x"}',
              fmt=RuntimeArtifactFormat.JSON_EVENTS,
              language=RuntimeFrameLanguage.JAVA),
])
def test_same_source_name_with_a_different_dedup_key_is_fatal(second):
    first = _artifact("ci.log", b"one\n")
    if second.format is RuntimeArtifactFormat.JSON_EVENTS:
        first = _artifact("ci.log", b'{"kind":"log","message":"x"}',
                          fmt=RuntimeArtifactFormat.JSON_EVENTS,
                          language=RuntimeFrameLanguage.PYTHON)
    with pytest.raises(RuntimeEvidenceValidationError) as caught:
        RuntimeEvidenceIngestor().ingest(_request(first, second))
    assert str(caught.value) == (
        "source_name is used by conflicting artifacts")


def test_ingest_requires_exact_request_and_graph_types():
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor().ingest(None)
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor().ingest(object())
    with pytest.raises(RuntimeEvidenceGraphError):
        RuntimeEvidenceIngestor().ingest(
            _request(_artifact("a", b"ok\n")), object())
    with pytest.raises(RuntimeEvidenceGraphError):
        RuntimeEvidenceIngestor().ingest(
            _request(_artifact("a", b"ok\n")), ())


def test_ingest_revalidates_bypassed_request_and_artifact_fields():
    listed = _request(_artifact("a", b"ok\n"))
    object.__setattr__(listed, "artifacts", [_artifact("a", b"ok\n")])
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor().ingest(listed)

    mutable = _request(_artifact("a", b"ok\n"))
    object.__setattr__(mutable.artifacts[0], "content", bytearray(b"ok\n"))
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor().ingest(mutable)

    surrogate = _request(_artifact("a", b"ok\n"))
    object.__setattr__(surrogate, "repository_namespace", "a\ud800b")
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor().ingest(surrogate)


def test_namespace_uses_the_effective_config_limit():
    config = RuntimeEvidenceConfig(max_namespace_bytes=4)
    inventory = RuntimeEvidenceIngestor(config).ingest(
        _request(namespace="éé"))
    assert inventory.repository_namespace == "éé"
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor(config).ingest(_request(namespace="ééa"))
    with pytest.raises(RuntimeEvidenceValidationError):
        RuntimeEvidenceIngestor(config).ingest(_request(namespace=""))


def test_dedup_retains_all_names_and_counts_duplicate_submissions():
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("b.log", b"same\n"),
        _artifact("A.log", b"same\n"),
        _artifact("A.log", b"same\n"),
    ))
    assert len(inv.artifacts) == 1
    assert inv.artifacts[0].source_names == ("A.log", "b.log")
    assert inv.artifacts[0].duplicate_count == 2
    assert inv.submitted_artifacts == 3
    assert inv.total_input_bytes == 5
    assert inv.artifacts[0].status is RuntimeArtifactStatus.COMPLETE


def test_artifact_order_is_canonical_and_independent_of_submission_order():
    artifacts = (
        _artifact("b.log", b"b\n"),
        _artifact("A.log", b"a\n"),
        _artifact("a.log", b"c\n"),
    )
    forward = RuntimeEvidenceIngestor().ingest(_request(*artifacts))
    reverse = RuntimeEvidenceIngestor().ingest(_request(*reversed(artifacts)))
    assert [artifact.source_names[0] for artifact in forward.artifacts] == [
        "A.log", "a.log", "b.log"]
    assert [
        (artifact.source_names, artifact.artifact_id,
         artifact.content_sha256)
        for artifact in forward.artifacts
    ] == [
        (artifact.source_names, artifact.artifact_id,
         artifact.content_sha256)
        for artifact in reverse.artifacts
    ]


def test_event_and_frame_ids_are_stable_when_submission_order_changes():
    text = _artifact("z.log", b"one\ntwo\n")
    stack = _artifact("a.stack", _STACK_TEXT,
                      RuntimeArtifactFormat.STACK_TRACE_TEXT,
                      RuntimeFrameLanguage.PYTHON)
    forward = RuntimeEvidenceIngestor().ingest(_request(text, stack))
    reverse = RuntimeEvidenceIngestor().ingest(_request(stack, text))
    assert len(forward.events) == 3
    assert len(forward.frames) == 2
    assert [
        (event.event_id, event.artifact_id, event.sequence)
        for event in forward.events
    ] == [
        (event.event_id, event.artifact_id, event.sequence)
        for event in reverse.events
    ]
    assert [
        (frame.frame_id, frame.event_id, frame.ordinal)
        for frame in forward.frames
    ] == [
        (frame.frame_id, frame.event_id, frame.ordinal)
        for frame in reverse.frames
    ]


def test_mixed_formats_have_exact_per_artifact_counts_and_global_order():
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("c.log", b"one\ntwo\n"),
        _artifact("a.json", b'{"kind":"log","message":"json"}',
                  RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("b.stack", _STACK_TEXT,
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
    ))
    assert [artifact.source_names[0] for artifact in inv.artifacts] == [
        "a.json", "b.stack", "c.log"]
    assert [artifact.format for artifact in inv.artifacts] == [
        RuntimeArtifactFormat.JSON_EVENTS,
        RuntimeArtifactFormat.STACK_TRACE_TEXT,
        RuntimeArtifactFormat.TEXT_LOG,
    ]
    assert [artifact.event_count for artifact in inv.artifacts] == [1, 1, 2]
    assert [artifact.frame_count for artifact in inv.artifacts] == [0, 2, 0]
    assert [(event.artifact_id, event.sequence) for event in inv.events] == [
        (inv.artifacts[0].artifact_id, 0),
        (inv.artifacts[1].artifact_id, 0),
        (inv.artifacts[2].artifact_id, 0),
        (inv.artifacts[2].artifact_id, 1),
    ]
    assert inv.total_events == 4
    assert inv.total_frames == 2
    assert inv.ingestion_complete is True


@pytest.mark.parametrize("name, fmt, content, language, status", [
    ("empty.log", RuntimeArtifactFormat.TEXT_LOG, b"", None,
     RuntimeArtifactStatus.COMPLETE),
    ("blank.log", RuntimeArtifactFormat.TEXT_LOG, b"\n \t\n", None,
     RuntimeArtifactStatus.COMPLETE),
    ("mixed.log", RuntimeArtifactFormat.TEXT_LOG, b"ok\n\xff\n", None,
     RuntimeArtifactStatus.PARTIAL),
    ("invalid.log", RuntimeArtifactFormat.TEXT_LOG, b"\xff\n", None,
     RuntimeArtifactStatus.FAILED),
    ("empty.json", RuntimeArtifactFormat.JSON_EVENTS, b"[]", None,
     RuntimeArtifactStatus.COMPLETE),
    ("blank.jsonl", RuntimeArtifactFormat.JSON_EVENTS, b"\n \n", None,
     RuntimeArtifactStatus.COMPLETE),
    ("empty.jsonl", RuntimeArtifactFormat.JSON_EVENTS, b"", None,
     RuntimeArtifactStatus.COMPLETE),
    ("invalid.json", RuntimeArtifactFormat.JSON_EVENTS, b'{"kind":"log"}',
     None, RuntimeArtifactStatus.FAILED),
    ("empty.stack", RuntimeArtifactFormat.STACK_TRACE_TEXT, b"",
     RuntimeFrameLanguage.PYTHON, RuntimeArtifactStatus.COMPLETE),
    ("blank.stack", RuntimeArtifactFormat.STACK_TRACE_TEXT, b"\n \n",
     RuntimeFrameLanguage.PYTHON, RuntimeArtifactStatus.COMPLETE),
])
def test_artifact_status_matrix(name, fmt, content, language, status):
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(name, content, fmt, language)))
    assert [(artifact.source_names[0], artifact.status)
            for artifact in inv.artifacts] == [(name, status)]
    assert inv.artifacts[0].status is status
    if status is RuntimeArtifactStatus.COMPLETE and content != b"ok\n\xff\n":
        assert inv.artifacts[0].event_count == 0
        assert inv.events == ()
    assert inv.artifacts[0].issue_count == len(
        _issues_for(inv, inv.artifacts[0].artifact_id))


def test_per_artifact_oversize_is_skipped_without_parsing(monkeypatch):
    import backend.runtime_evidence.ingestor as ingestor

    calls = []
    monkeypatch.setattr(
        ingestor, "parse_text_log",
        lambda *args, **kwargs: calls.append(args))
    config = RuntimeEvidenceConfig(
        max_artifact_bytes=3, max_total_input_bytes=10,
        max_submitted_input_bytes=10)
    inv = RuntimeEvidenceIngestor(config).ingest(
        _request(_artifact("big.log", b"abcd")))
    assert calls == []
    assert [(artifact.status, artifact.size_bytes, artifact.event_count,
             artifact.issue_count) for artifact in inv.artifacts] == [
        (RuntimeArtifactStatus.SKIPPED, 4, 0, 1)]
    artifact = inv.artifacts[0]
    assert artifact.status is RuntimeArtifactStatus.SKIPPED
    assert artifact.size_bytes == 4
    assert artifact.event_count == 0
    assert artifact.issue_count == 1
    assert inv.events == ()
    assert inv.frames == ()
    assert [(issue.code, issue.event_sequence, issue.source_line)
            for issue in inv.issues] == [
        (RuntimeIssueCode.ARTIFACT_TOO_LARGE, None, None)]
    assert inv.incomplete_reasons == (
        RuntimeIncompleteReason.ARTIFACT_SKIPPED,)
    assert inv.ingestion_complete is False


def test_events_frames_and_issues_match_authoritative_counts():
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("a.log", b"ok\n\xff\n"),
        _artifact("b.json", b'[]', RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("c.stack", b'  File "pkg/a.py", line 1, in f\n',
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
    ))
    assert inv.submitted_artifacts == 3
    assert inv.duplicate_artifacts == 0
    assert inv.submitted_artifacts == (
        len(inv.artifacts) + inv.duplicate_artifacts)
    assert inv.total_input_bytes == sum(
        artifact.size_bytes for artifact in inv.artifacts)
    assert inv.total_events == len(inv.events) == sum(
        artifact.event_count for artifact in inv.artifacts)
    assert inv.total_frames == len(inv.frames) == sum(
        artifact.frame_count for artifact in inv.artifacts)
    for artifact in inv.artifacts:
        events = tuple(
            event for event in inv.events
            if event.artifact_id == artifact.artifact_id)
        issues = _issues_for(inv, artifact.artifact_id)
        assert artifact.event_count == len(events)
        assert artifact.frame_count == sum(
            len(event.frames) for event in events)
        assert artifact.issue_count == len(issues)
        assert artifact.text_truncated == any(
            event.text_truncated for event in events)
        assert artifact.frames_truncated == any(
            event.frames_truncated for event in events)
    assert sum(artifact.issue_count for artifact in inv.artifacts) == len(
        inv.issues)


def test_source_lines_and_ordinals_are_one_based_and_zero_based():
    jsonl = (
        b'{"kind":"log","message":"a"}\n'
        b'{"kind":"log","message":"b"}\n'
    )
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("a.log", b"one\n\ntwo\n"),
        _artifact("b.jsonl", jsonl, RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("c.stack", _STACK_TEXT,
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
    ))
    text_events = tuple(
        event for event in inv.events
        if event.artifact_id == inv.artifacts[0].artifact_id)
    assert [(event.sequence, event.source_line) for event in text_events] == [
        (0, 1), (1, 3)]
    jsonl_events = tuple(
        event for event in inv.events
        if event.artifact_id == inv.artifacts[1].artifact_id)
    assert [(event.sequence, event.source_line) for event in jsonl_events] == [
        (0, 1), (1, 2)]
    stack_events = tuple(
        event for event in inv.events
        if event.artifact_id == inv.artifacts[2].artifact_id)
    assert len(stack_events) == 1
    assert stack_events[0].sequence == 0
    assert stack_events[0].source_line == 1
    assert [(frame.ordinal, frame.line)
            for frame in stack_events[0].frames] == [(0, 10), (1, 20)]


def test_issues_are_deduplicated_and_canonically_ordered():
    document = (
        b'[{"kind":"log","message":"'
        + b"x" * 20
        + b'"}, 3]'
    )
    config = RuntimeEvidenceConfig(max_message_bytes=8)
    inv = RuntimeEvidenceIngestor(config).ingest(_request(
        _artifact("a.document.json", document,
                  RuntimeArtifactFormat.JSON_EVENTS),
        _artifact("b.lines.jsonl", b"not json\n",
                  RuntimeArtifactFormat.JSON_EVENTS),
    ))
    assert [(issue.code, issue.event_sequence, issue.source_line)
            for issue in inv.issues] == [
        (RuntimeIssueCode.TEXT_TRUNCATED, 0, None),
        (RuntimeIssueCode.INVALID_EVENT_SCHEMA, None, None),
        (RuntimeIssueCode.MALFORMED_JSON, None, 1),
    ]
    assert inv.artifacts[0].issue_count == 2
    assert inv.artifacts[1].issue_count == 1


def test_duplicate_invalid_source_location_issues_collapse():
    content = (
        b'{"kind":"error","message":"x","error":{"frames":['
        b'{"source":"a//b","line":1},{"source":"a//b","line":1}]}}'
    )
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("bad.json", content, RuntimeArtifactFormat.JSON_EVENTS)))
    assert [issue.code for issue in inv.issues] == [
        RuntimeIssueCode.INVALID_SOURCE_LOCATION]
    assert len(inv.events) == 1
    assert len(inv.events[0].frames) == 2
    assert inv.artifacts[0].status is RuntimeArtifactStatus.PARTIAL


def test_claims_event_text_flag_and_fingerprint_use_final_event_fields():
    from backend.runtime_evidence.identities import event_fingerprint

    claims = dict(
        declared_revision="rev-123",
        declared_captured_at="2026-10-27T00:00:00Z",
        declared_capture_completeness=CaptureCompleteness.PARTIAL,
    )
    content = b'{"kind":"log","message":"hello","trace_id":"trace-1"}'
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact("a.json", content, RuntimeArtifactFormat.JSON_EVENTS),
        **claims))
    assert inv.declared_revision == "rev-123"
    assert inv.declared_captured_at == "2026-10-27T00:00:00Z"
    assert inv.declared_capture_completeness is CaptureCompleteness.PARTIAL
    assert inv.event_text_retained is True
    assert len(inv.events) == 1
    event = inv.events[0]
    assert event.message == "hello"
    assert event.event_fingerprint == event_fingerprint(
        repository_namespace="repo", kind=event.kind,
        timestamp_text=event.timestamp_text, level=event.level,
        message=event.message, logger=event.logger,
        operation=event.operation, trace_id=event.trace_id,
        span_id=event.span_id, parent_span_id=event.parent_span_id,
        duration_ms_text=event.duration_ms_text, status=event.status,
        error_type=event.error_type, error_message=event.error_message,
        tags=event.tags, frames=event.frames)

    metadata = RuntimeEvidenceIngestor(
        RuntimeEvidenceConfig(retain_event_text=False)).ingest(_request(
            _artifact("a.json", content, RuntimeArtifactFormat.JSON_EVENTS)))
    assert metadata.event_text_retained is False
    assert metadata.events[0].message is None
    assert metadata.artifacts[0].artifact_id == inv.artifacts[0].artifact_id
    assert metadata.events[0].event_id == event.event_id


def test_graph_binding_not_provided_matched_and_mismatch():
    request = _request(_artifact("a.log", b"ok\n"))
    absent = RuntimeEvidenceIngestor().ingest(request)
    assert absent.graph_binding is GraphBinding.NOT_PROVIDED
    matched = RuntimeEvidenceIngestor().ingest(request, _graph())
    assert matched.graph_binding is GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
    with pytest.raises(RuntimeEvidenceGraphError):
        RuntimeEvidenceIngestor().ingest(
            request, _graph(namespace="other"))


def test_graph_namespace_uses_the_effective_limit():
    config = RuntimeEvidenceConfig(max_namespace_bytes=4)
    request = _request(namespace="abcd")
    matched = RuntimeEvidenceIngestor(config).ingest(
        request, _graph(namespace="abcd"))
    assert matched.graph_binding is GraphBinding.NAMESPACE_MATCHED_UNVERSIONED
    with pytest.raises(RuntimeEvidenceGraphError):
        RuntimeEvidenceIngestor(config).ingest(
            request, _graph(namespace="abcde"))


def test_inventory_retains_no_bytes_and_repr_is_redacted():
    namespace = "CP2-PRIVATE-NAMESPACE"
    source = "CP2-PRIVATE-SOURCE.log"
    content = b"CP2-PRIVATE-MESSAGE\n"
    revision = "CP2-PRIVATE-REVISION"
    captured = "CP2-PRIVATE-CAPTURED"
    inv = RuntimeEvidenceIngestor().ingest(_request(
        _artifact(source, content),
        namespace=namespace, declared_revision=revision,
        declared_captured_at=captured))
    assert len(inv.events) == 1
    assert inv.events[0].message == "CP2-PRIVATE-MESSAGE"
    rendered = repr(inv)
    for sentinel in (
        namespace, source, "CP2-PRIVATE-MESSAGE", revision, captured,
    ):
        assert sentinel not in rendered
    from dataclasses import fields
    for record in (*inv.artifacts, *inv.events, *inv.frames, *inv.issues):
        assert not any(
            type(getattr(record, field.name)) is bytes
            for field in fields(record))
    assert not any(
        type(getattr(inv, field.name)) is bytes for field in fields(inv))
