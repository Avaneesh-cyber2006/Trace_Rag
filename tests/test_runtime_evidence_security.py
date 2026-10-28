"""Security and contract tests for runtime evidence logging and sanitization."""

import logging
import pathlib
import traceback

import pytest

import backend.runtime_evidence as package
import backend.runtime_evidence.config as config_module
import backend.runtime_evidence.ingestor as ingestor_module
from backend.runtime_evidence import (
    CaptureCompleteness,
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeArtifactStatus,
    RuntimeEvidenceConfig,
    RuntimeEvidenceConfigurationError,
    RuntimeEvidenceGraphError,
    RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError,
    RuntimeEvidenceLimitError,
    RuntimeEvidenceRequest,
    RuntimeEvidenceValidationError,
    RuntimeFrameLanguage,
    ingest_runtime_evidence,
)
from backend.runtime_evidence.ingestor import RuntimeEvidenceIngestor

_CONFIG_LOGGER = "backend.runtime_evidence.config"
_INGESTOR_LOGGER = "backend.runtime_evidence.ingestor"
_STACK_TEXT = (
    b'  File "pkg/a.py", line 10, in f\n'
    b'  File "pkg/b.py", line 20, in g\n'
    b"ValueError: boom\n"
)


def _artifact(
    name="ci.log",
    content=b"one\n",
    fmt=RuntimeArtifactFormat.TEXT_LOG,
    language=None,
):
    return RuntimeArtifact(
        source_name=name,
        format=fmt,
        content=content,
        declared_language=language,
    )


def _request(*artifacts, namespace="repo", **claims):
    return RuntimeEvidenceRequest(namespace, tuple(artifacts), **claims)


def _issue_codes(inventory, artifact_id):
    return sorted(
        issue.code.value
        for issue in inventory.issues
        if issue.artifact_id == artifact_id
    )


def _error_messages(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.ERROR
    ]


@pytest.mark.parametrize("build, expected_message", [
    (lambda: RuntimeEvidenceConfig(max_events=0),
     "max_events must be positive"),
    (lambda: RuntimeEvidenceConfig(max_events="5"),
     "max_events must be an exact int"),
    (lambda: RuntimeEvidenceConfig(max_events=True),
     "max_events must be an exact int"),
    (lambda: RuntimeEvidenceConfig(retain_event_text=1),
     "retain_event_text must be an exact bool"),
    (lambda: RuntimeEvidenceConfig(
        max_artifact_bytes=11,
        max_total_input_bytes=10,
        max_submitted_input_bytes=10,
    ), "max_artifact_bytes must not exceed max_total_input_bytes"),
    (lambda: RuntimeEvidenceConfig(
        max_frames_per_event=3,
        max_total_frames=2,
    ), "max_frames_per_event must not exceed max_total_frames"),
])
def test_invalid_configuration_logs_one_fixed_error(caplog, build,
                                                    expected_message):
    with caplog.at_level(logging.ERROR, logger=_CONFIG_LOGGER):
        with pytest.raises(RuntimeEvidenceConfigurationError) as caught:
            build()
    assert str(caught.value) == expected_message
    assert [(record.name, record.levelno, record.getMessage())
            for record in caplog.records] == [
        (_CONFIG_LOGGER, logging.ERROR,
         "runtime evidence configuration failed")]
    record = caplog.records[0]
    assert record.args == ()
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None


def test_non_exact_constructor_config_logs_once_from_ingestor(caplog):
    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceConfigurationError) as caught:
            RuntimeEvidenceIngestor(object())
    assert str(caught.value) == "config must be an exact RuntimeEvidenceConfig"
    assert [(record.name, record.getMessage())
            for record in caplog.records] == [
        (_INGESTOR_LOGGER, "runtime evidence configuration failed")]
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert record.args == ()
    assert record.exc_info is None
    assert record.exc_text is None
    assert record.stack_info is None


def test_convenience_config_rejection_does_not_log_twice(caplog):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeEvidenceConfigurationError):
            ingest_runtime_evidence(_request(_artifact()), config=object())
    assert [(record.name, record.getMessage())
            for record in caplog.records] == [
        (_INGESTOR_LOGGER, "runtime evidence configuration failed")]


def test_info_logs_start_and_final_aggregate_counts(caplog):
    request = _request(
        _artifact("a.log", b"one\ntwo\n"),
        _artifact("a-copy.log", b"one\ntwo\n"),
        _artifact("b.stack", _STACK_TEXT,
                  RuntimeArtifactFormat.STACK_TRACE_TEXT,
                  RuntimeFrameLanguage.PYTHON),
        _artifact("c.log", b"ok\n\xff\n"),
    )
    with caplog.at_level(logging.INFO, logger=_INGESTOR_LOGGER):
        inventory = ingest_runtime_evidence(request)
    info_records = [
        record for record in caplog.records
        if record.levelno == logging.INFO
    ]
    assert [record.name for record in info_records] == [
        _INGESTOR_LOGGER, _INGESTOR_LOGGER]
    assert [record.getMessage() for record in info_records] == [
        "runtime evidence ingestion started",
        "runtime evidence ingestion completed "
        "artifacts=3 events=4 frames=2 issues=1",
    ]
    assert len(inventory.artifacts) == 3
    assert len(inventory.events) == 4
    assert len(inventory.frames) == 2
    assert len(inventory.issues) == 1


def test_debug_logs_one_artifact_record_with_ids_status_and_codes(caplog):
    request = _request(
        _artifact("complete.log", b"ok\n"),
        _artifact("partial.log", b"ok\n\xff\n"),
    )
    with caplog.at_level(logging.DEBUG, logger=_INGESTOR_LOGGER):
        inventory = ingest_runtime_evidence(request)
    debug_records = [
        record for record in caplog.records
        if record.levelno == logging.DEBUG
    ]
    expected = [
        (
            "runtime evidence artifact "
            f"artifact_id={artifact.artifact_id} "
            f"format={artifact.format.value} "
            f"status={artifact.status.value} "
            f"events={artifact.event_count} "
            f"frames={artifact.frame_count} "
            f"issues={','.join(_issue_codes(inventory, artifact.artifact_id))}"
        )
        for artifact in inventory.artifacts
    ]
    assert [record.getMessage() for record in debug_records] == expected
    assert all(record.name == _INGESTOR_LOGGER for record in debug_records)
    assert [record.args for record in debug_records] == [
        (
            inventory.artifacts[0].artifact_id,
            "text_log",
            "complete",
            1,
            0,
            "",
        ),
        (
            inventory.artifacts[1].artifact_id,
            "text_log",
            "partial",
            1,
            0,
            "invalid_utf8",
        ),
    ]
    assert "complete.log" not in caplog.text
    assert "partial.log" not in caplog.text


def test_warning_logs_once_for_each_degraded_artifact(caplog):
    config = RuntimeEvidenceConfig(
        max_artifact_bytes=8,
        max_total_input_bytes=100,
        max_submitted_input_bytes=100,
        max_message_bytes=4,
    )
    request = _request(
        _artifact("complete.log", b"ok\n"),
        _artifact("failed.log", b"\xff\n"),
        _artifact("partial.log", b"ok\n\xff\n"),
        _artifact("skipped.log", b"123456789"),
        _artifact("truncated.log", b"abcde\n"),
    )
    with caplog.at_level(logging.WARNING, logger=_INGESTOR_LOGGER):
        inventory = ingest_runtime_evidence(request, config=config)
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    degraded = [
        artifact for artifact in inventory.artifacts
        if artifact.status is not RuntimeArtifactStatus.COMPLETE
        or artifact.text_truncated
        or artifact.frames_truncated
    ]
    assert [artifact.status for artifact in degraded] == [
        RuntimeArtifactStatus.FAILED,
        RuntimeArtifactStatus.PARTIAL,
        RuntimeArtifactStatus.SKIPPED,
        RuntimeArtifactStatus.PARTIAL,
    ]
    assert [record.getMessage() for record in warnings] == [
        (
            "runtime evidence artifact warning "
            f"artifact_id={artifact.artifact_id} "
            f"status={artifact.status.value} "
            f"issues={','.join(_issue_codes(inventory, artifact.artifact_id))}"
        )
        for artifact in degraded
    ]
    assert all(record.name == _INGESTOR_LOGGER for record in warnings)
    assert _issue_codes(inventory, degraded[0].artifact_id) == [
        "invalid_utf8"]
    assert _issue_codes(inventory, degraded[1].artifact_id) == [
        "invalid_utf8"]
    assert _issue_codes(inventory, degraded[2].artifact_id) == [
        "artifact_too_large"]
    assert _issue_codes(inventory, degraded[3].artifact_id) == [
        "text_truncated"]
    assert degraded[3].text_truncated is True


def test_issue_limit_loss_warns_without_changing_evidence(caplog):
    request = _request(_artifact("many.log", b"ok\n\xff\n\xff\n"))
    baseline = ingest_runtime_evidence(request)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=_INGESTOR_LOGGER):
        truncated = ingest_runtime_evidence(
            request, config=RuntimeEvidenceConfig(max_issues=1))
    assert baseline.artifacts[0].status is RuntimeArtifactStatus.PARTIAL
    assert truncated.artifacts[0].status is baseline.artifacts[0].status
    assert truncated.artifacts[0].event_count == (
        baseline.artifacts[0].event_count)
    assert truncated.artifacts[0].frame_count == (
        baseline.artifacts[0].frame_count)
    assert truncated.events == baseline.events
    assert truncated.issues_truncated is True
    assert [issue.code.value for issue in truncated.issues] == [
        "invalid_utf8", "issue_limit_reached"]
    assert truncated.artifacts[0].issue_count == 2
    warnings = [
        record for record in caplog.records
        if record.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    artifact = truncated.artifacts[0]
    assert warnings[0].getMessage() == (
        "runtime evidence artifact warning "
        f"artifact_id={artifact.artifact_id} status=partial "
        "issues=invalid_utf8,issue_limit_reached")


def test_fatal_categories_log_exact_fixed_error_tokens(caplog, monkeypatch):
    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceValidationError) as caught:
            ingest_runtime_evidence(object())
    assert str(caught.value) == (
        "request must be an exact RuntimeEvidenceRequest")
    assert _error_messages(caplog) == ["runtime evidence validation failed"]
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceLimitError) as caught:
            ingest_runtime_evidence(
                _request(
                    _artifact("a.log", b"a\n"),
                    _artifact("b.log", b"b\n"),
                ),
                config=RuntimeEvidenceConfig(max_artifacts=1),
            )
    assert str(caught.value) == (
        "submitted artifact count exceeds the configured limit")
    assert _error_messages(caplog) == ["runtime evidence limit failed"]
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceGraphError) as caught:
            ingest_runtime_evidence(_request(_artifact()), graph=object())
    assert str(caught.value) == "runtime evidence graph is invalid"
    assert _error_messages(caplog) == ["runtime evidence graph failed"]
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger=_CONFIG_LOGGER):
        with pytest.raises(RuntimeEvidenceConfigurationError) as caught:
            RuntimeEvidenceConfig(max_events=0)
    assert str(caught.value) == "max_events must be positive"
    assert _error_messages(caplog) == [
        "runtime evidence configuration failed"]
    caplog.clear()

    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceConfigurationError) as caught:
            ingest_runtime_evidence(_request(), config=object())
    assert str(caught.value) == "config must be an exact RuntimeEvidenceConfig"
    assert _error_messages(caplog) == [
        "runtime evidence configuration failed"]
    caplog.clear()

    def explode(*args, **kwargs):
        raise RuntimeError("CP18-INTERNAL")

    monkeypatch.setattr(ingestor_module, "_parse_artifact", explode)
    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceIngestionError) as caught:
            ingest_runtime_evidence(_request(_artifact()))
    assert str(caught.value) == "runtime evidence ingestion failed"
    assert _error_messages(caplog) == ["runtime evidence ingestion failed"]


@pytest.mark.parametrize("exception_type", [
    RuntimeError,
    ValueError,
    RuntimeEvidenceInvariantError,
    MemoryError,
])
def test_unexpected_internal_errors_are_sanitized_and_unchained(
        caplog, monkeypatch, exception_type):
    sentinel = "CP18-INTERNAL-SENTINEL"

    def explode(*args, **kwargs):
        raise exception_type(sentinel)

    monkeypatch.setattr(ingestor_module, "_parse_artifact", explode)
    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(RuntimeEvidenceIngestionError) as caught:
            ingest_runtime_evidence(
                _request(_artifact("secret.log", b"x\n")))
    error = caught.value
    assert type(error) is RuntimeEvidenceIngestionError
    assert str(error) == "runtime evidence ingestion failed"
    assert error.args == ("runtime evidence ingestion failed",)
    assert sentinel not in str(error)
    assert sentinel not in repr(error)
    assert error.__cause__ is None
    assert error.__suppress_context__ is True
    formatted = "".join(traceback.format_exception(
        type(error), error, error.__traceback__))
    assert sentinel not in formatted
    records = [
        record for record in caplog.records
        if record.levelno == logging.ERROR
    ]
    assert [record.getMessage() for record in records] == [
        "runtime evidence ingestion failed"]
    assert records[0].name == _INGESTOR_LOGGER
    assert records[0].args == ()
    for record in caplog.records:
        assert record.exc_info is None
        assert record.exc_text is None
        assert record.stack_info is None


@pytest.mark.parametrize("exception_type", [
    KeyboardInterrupt,
    SystemExit,
    GeneratorExit,
])
def test_base_exceptions_are_not_swallowed(caplog, monkeypatch,
                                           exception_type):
    def explode(*args, **kwargs):
        raise exception_type()

    monkeypatch.setattr(ingestor_module, "_parse_artifact", explode)
    with caplog.at_level(logging.ERROR, logger=_INGESTOR_LOGGER):
        with pytest.raises(exception_type):
            ingest_runtime_evidence(_request(_artifact()))
    assert _error_messages(caplog) == []


def test_hostile_sentinels_never_reach_logs_or_public_reprs(caplog):
    source_one = "CP18-SOURCE-NAME-ONE.log"
    source_two = "CP18-SOURCE-NAME-TWO.log"
    source_three = "CP18-SOURCE-NAME-THREE.log"
    namespace = "CP18-NAMESPACE"
    revision = "CP18-REVISION"
    captured_at = "CP18-CAPTURED-AT"
    message = "CP18-MESSAGE"
    tag_key = "CP18-TAG-KEY"
    tag_value = "CP18-TAG-VALUE"
    trace_id = "CP18-TRACE-ID"
    span_id = "CP18-SPAN-ID"
    function_text = "CP18-FUNCTION-TEXT"
    path = "pkg/CP18-PATH-TEXT.py"
    stack_text = "CP18-STACK-TEXT"
    raw_bytes = "CP18-RAW-BYTES"
    sentinels = (
        "CP18-SOURCE-NAME",
        namespace,
        revision,
        captured_at,
        message,
        tag_key,
        tag_value,
        trace_id,
        span_id,
        function_text,
        path,
        stack_text,
        raw_bytes,
    )
    request = RuntimeEvidenceRequest(
        namespace,
        (
            RuntimeArtifact(
                source_one,
                RuntimeArtifactFormat.JSON_EVENTS,
                (
                    '{"kind":"log","message":"' + message + '",'
                    '"tags":{"' + tag_key + '":"' + tag_value + '"},'
                    '"trace_id":"' + trace_id + '",'
                    '"span_id":"' + span_id + '"}'
                ).encode(),
            ),
            RuntimeArtifact(
                source_two,
                RuntimeArtifactFormat.STACK_TRACE_TEXT,
                (
                    f'  File "{path}", line 4, in {function_text}\n'
                    f"ValueError: {stack_text}\n"
                ).encode(),
                RuntimeFrameLanguage.PYTHON,
            ),
            RuntimeArtifact(
                source_three,
                RuntimeArtifactFormat.TEXT_LOG,
                (raw_bytes + "\n").encode(),
            ),
        ),
        declared_revision=revision,
        declared_captured_at=captured_at,
        declared_capture_completeness=CaptureCompleteness.PARTIAL,
    )
    with caplog.at_level(logging.DEBUG, logger=_INGESTOR_LOGGER):
        inventory = ingest_runtime_evidence(request)
    assert inventory.repository_namespace == namespace
    assert inventory.declared_revision == revision
    assert inventory.declared_captured_at == captured_at
    assert {artifact.source_names[0] for artifact in inventory.artifacts} == {
        source_one, source_two, source_three}
    assert message in {event.message for event in inventory.events}
    assert trace_id in {event.trace_id for event in inventory.events}
    assert span_id in {event.span_id for event in inventory.events}
    assert tag_key in {
        tag.key for event in inventory.events for tag in event.tags}
    assert tag_value in {
        tag.value for event in inventory.events for tag in event.tags}
    assert function_text in {
        frame.function_text for frame in inventory.frames}
    assert path in {frame.relative_path for frame in inventory.frames}
    assert any(
        event.message is not None and stack_text in event.message
        for event in inventory.events
    )
    assert raw_bytes in {event.message for event in inventory.events}

    assert caplog.records
    for sentinel in sentinels:
        assert sentinel not in caplog.text
    for record in caplog.records:
        rendered = record.getMessage()
        argument_text = str(record.args) + repr(record.args)
        message_text = str(record.msg)
        exc_text = record.exc_text or ""
        for sentinel in sentinels:
            assert sentinel not in rendered
            assert sentinel not in argument_text
            assert sentinel not in message_text
            assert sentinel not in exc_text
        assert record.exc_info is None
        assert record.exc_text is None
        assert record.stack_info is None
        for argument in record.args:
            assert type(argument) in (str, int)

    values = (
        inventory,
        *inventory.artifacts,
        *inventory.events,
        *inventory.frames,
        *inventory.issues,
    )
    for value in values:
        text = repr(value) + str(value)
        for sentinel in sentinels:
            assert sentinel not in text, (type(value).__name__, sentinel)

    with pytest.raises(RuntimeEvidenceGraphError) as caught:
        ingest_runtime_evidence(request, graph=object())
    assert str(caught.value) == "runtime evidence graph is invalid"
    for sentinel in sentinels:
        assert sentinel not in str(caught.value)
        assert sentinel not in repr(caught.value)


def test_only_config_and_ingestor_define_module_loggers():
    package_dir = pathlib.Path(package.__file__).resolve().parent
    offenders = sorted(
        path.name
        for path in package_dir.glob("*.py")
        if path.name not in {"config.py", "ingestor.py"}
        and "logging" in path.read_text(encoding="utf-8")
    )
    assert offenders == []
    assert config_module.logger is logging.getLogger(_CONFIG_LOGGER)
    assert ingestor_module.logger is logging.getLogger(_INGESTOR_LOGGER)


def test_repeated_calls_preserve_event_budget_and_add_no_handlers():
    logger = logging.getLogger(_INGESTOR_LOGGER)
    before_handlers = tuple(logger.handlers)
    request = _request(_artifact("one.log", b"one\ntwo\nthree\n"))
    config = RuntimeEvidenceConfig(max_events=2)
    ingestor = RuntimeEvidenceIngestor(config)
    first = ingestor.ingest(request)
    second = ingestor.ingest(request)
    assert first == second
    assert first is not second
    assert first.total_events == 2
    assert first.events_truncated is True
    assert [issue.code.value for issue in first.issues] == [
        "event_limit_reached"]
    assert tuple(logger.handlers) == before_handlers


def test_repeated_calls_preserve_issue_limit_sentinel():
    request = _request(_artifact("many.log", b"\xff\n\xff\n\xff\n"))
    config = RuntimeEvidenceConfig(max_issues=1)
    first = ingest_runtime_evidence(request, config=config)
    second = ingest_runtime_evidence(request, config=config)
    assert first == second
    assert first is not second
    assert first.issues_truncated is True
    assert [issue.code.value for issue in first.issues] == [
        "invalid_utf8", "issue_limit_reached"]
    assert first.artifacts[0].issue_count == 2
