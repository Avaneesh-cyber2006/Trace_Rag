"""Security and contract tests for runtime evidence logging and sanitization."""

import ast
import builtins
import importlib
import io
import logging
import marshal
import os
import pathlib
import pickle
import socket
import subprocess
import sys
import traceback
import urllib.request
from dataclasses import fields
from pathlib import Path

import pytest

import backend.runtime_evidence as package
import backend.runtime_evidence.config as config_module
import backend.runtime_evidence.ingestor as ingestor_module
from backend.runtime_evidence import (
    CaptureCompleteness,
    FrameAssociation,
    PathMatchBasis,
    RuntimeArtifact,
    RuntimeArtifactFormat,
    RuntimeArtifactStatus,
    RuntimeEventKind,
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


# --- Task 20: in-memory boundary, fatal errors, and static source audit ---


def _forbid(*args, **kwargs):
    raise AssertionError("Module 8 crossed the in-memory boundary")


def _patch_io(guard):
    for owner, name in (
        (builtins, "open"), (io, "open"), (os, "open"),
        (os, "scandir"), (os, "listdir"), (os, "remove"), (os, "unlink"),
        (os, "rename"), (os, "replace"), (os, "mkdir"), (os, "makedirs"),
        (os, "rmdir"), (os, "chmod"), (os, "symlink"), (os, "link"),
        (os, "truncate"), (os, "fdopen"),
        (Path, "open"), (Path, "read_text"), (Path, "read_bytes"),
        (Path, "write_text"), (Path, "write_bytes"), (Path, "stat"),
        (Path, "resolve"), (Path, "iterdir"), (Path, "unlink"),
        (Path, "rename"), (Path, "replace"), (Path, "mkdir"),
        (Path, "rmdir"), (Path, "touch"), (Path, "chmod"),
        (Path, "symlink_to"), (Path, "hardlink_to"),
        (subprocess, "run"), (subprocess, "Popen"), (subprocess, "call"),
        (subprocess, "check_call"), (subprocess, "check_output"),
        (socket, "socket"), (socket, "create_connection"),
        (urllib.request, "urlopen"), (importlib, "import_module"),
        (builtins, "eval"), (builtins, "exec"), (builtins, "compile"),
        (pickle, "loads"), (pickle, "load"),
        (marshal, "loads"), (marshal, "load"),
    ):
        guard.setattr(owner, name, _forbid)


def _security_request():
    stack = (
        b'Traceback (most recent call last):\n'
        b'  File "pkg/app.py", line 5, in run\n'
        b'ValueError: ' + b"SENTINEL-SECURITY" + b"\n"
    )
    return RuntimeEvidenceRequest(
        "repo",
        (
            RuntimeArtifact("secret.log", RuntimeArtifactFormat.TEXT_LOG,
                            b"SENTINEL-SECURITY\n"),
            RuntimeArtifact("trace.log", RuntimeArtifactFormat.STACK_TRACE_TEXT,
                            stack, RuntimeFrameLanguage.PYTHON),
        ),
    )


def test_ingestion_performs_no_io_execution_or_network(monkeypatch):
    request = _security_request()
    with monkeypatch.context() as guard:
        _patch_io(guard)
        inventory = RuntimeEvidenceIngestor().ingest(request)
        repeated = RuntimeEvidenceIngestor().ingest(request)
    assert inventory.total_events == 2
    assert len(inventory.events) == 2
    assert len(inventory.frames) == 1
    assert [event.kind for event in inventory.events] == [
        RuntimeEventKind.LOG, RuntimeEventKind.ERROR]
    assert [event.sequence for event in inventory.events] == [0, 0]
    assert all(
        len(event.event_id) == 64 and len(event.event_fingerprint) == 64
        for event in inventory.events)
    assert [event.event_id for event in repeated.events] == [
        event.event_id for event in inventory.events]
    frame = inventory.frames[0]
    assert frame.event_id == inventory.events[1].event_id
    assert frame.frame_id == repeated.frames[0].frame_id
    assert frame.event_id == repeated.frames[0].event_id
    assert frame.ordinal == 0
    assert frame.language is RuntimeFrameLanguage.PYTHON
    assert frame.function_text == "run"
    assert frame.relative_path == "pkg/app.py"
    assert frame.line == 5
    assert frame.column is None
    assert frame.path_match_basis is PathMatchBasis.UNVERIFIED_RELATIVE_PATH
    assert frame.association is FrameAssociation.NOT_EVALUATED
    assert frame.primary_node_id is None
    assert frame.candidate_node_ids == ()


def test_fatal_errors_are_fixed_and_do_not_echo_values():
    sentinel = "SENTINEL-ERROR-VALUE"
    with pytest.raises(RuntimeEvidenceValidationError) as caught:
        RuntimeEvidenceRequest(sentinel + "\ud800", ())
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None

    with pytest.raises(RuntimeEvidenceConfigurationError) as caught:
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(max_artifacts=0))
    assert sentinel not in str(caught.value)

    request = RuntimeEvidenceRequest(
        "repo",
        (RuntimeArtifact(sentinel, RuntimeArtifactFormat.TEXT_LOG, b"a\n"),
         RuntimeArtifact(sentinel + "2", RuntimeArtifactFormat.TEXT_LOG, b"b\n")),
    )
    with pytest.raises(RuntimeEvidenceLimitError) as caught:
        RuntimeEvidenceIngestor(RuntimeEvidenceConfig(max_artifacts=1)).ingest(
            request)
    assert sentinel not in str(caught.value)
    assert caught.value.__cause__ is None


def test_fatal_errors_log_and_raise_only_fixed_content_free_tokens(caplog):
    sentinel = "SENTINEL-FATAL-LOG-VALUE"
    valid_request = _request(_artifact())
    forged_request = object.__new__(RuntimeEvidenceRequest)
    for field in fields(RuntimeEvidenceRequest):
        object.__setattr__(
            forged_request, field.name, getattr(valid_request, field.name))
    object.__setattr__(
        forged_request, "repository_namespace", sentinel + "\ud800")

    cases = (
        (
            RuntimeEvidenceConfigurationError,
            lambda: RuntimeEvidenceConfig(max_events=sentinel),
        ),
        (
            RuntimeEvidenceValidationError,
            lambda: RuntimeEvidenceIngestor().ingest(forged_request),
        ),
        (
            RuntimeEvidenceLimitError,
            lambda: RuntimeEvidenceIngestor(
                RuntimeEvidenceConfig(max_artifacts=1)).ingest(
                RuntimeEvidenceRequest(
                    "repo",
                    (
                        RuntimeArtifact(
                            sentinel + "-a.log",
                            RuntimeArtifactFormat.TEXT_LOG, b"a\n"),
                        RuntimeArtifact(
                            sentinel + "-b.log",
                            RuntimeArtifactFormat.TEXT_LOG, b"b\n"),
                    ),
                )),
        ),
        (
            RuntimeEvidenceGraphError,
            lambda: RuntimeEvidenceIngestor().ingest(
                RuntimeEvidenceRequest(
                    sentinel + "-namespace", (_artifact(),)),
                graph=object(),
            ),
        ),
    )
    for error_type, build in cases:
        caplog.clear()
        with caplog.at_level(logging.ERROR):
            with pytest.raises(error_type) as caught:
                build()
        assert sentinel not in str(caught.value)
        assert sentinel not in repr(caught.value)
        assert caught.value.__cause__ is None
        assert caplog.records
        for record in caplog.records:
            assert sentinel not in record.getMessage()
            assert sentinel not in str(record.args)
            assert sentinel not in repr(record.args)
            assert sentinel not in str(record.msg)
            assert record.exc_info is None
            assert record.exc_text is None
            assert record.stack_info is None


def _module_name_for(path, root):
    return ".".join(path.relative_to(root).with_suffix("").parts)


def _import_base(node, package):
    if not node.level:
        return node.module or ""
    parts = package.split(".") if package else []
    keep = len(parts) - (node.level - 1)
    base = parts[:keep]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _imported_modules(tree, module_name):
    package = module_name.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                yield item.name
        elif isinstance(node, ast.ImportFrom):
            base = _import_base(node, package)
            for item in node.names:
                if item.name == "*":
                    yield base
                else:
                    yield f"{base}.{item.name}".strip(".")


FORBIDDEN_IMPORT_ROOTS = {
    "os", "pathlib", "subprocess", "socket", "urllib", "http", "requests",
    "httpx", "sqlite3", "chromadb", "git", "pickle", "marshal", "yaml",
    "joblib", "cloudpickle", "dill", "torch", "numpy", "google",
    "backend.code_parser", "backend.file_scanner", "backend.code_chunker",
    "backend.embedding_vector_store", "backend.git_history",
    "backend.repository_loader",
}


_PUBLIC_CODE_GRAPH_NAMES = frozenset({
    "CodeGraphInventory", "GraphNode", "GraphNodeKind",
})


def _is_allowed_module(name):
    root = name.split(".", 1)[0]
    if root in sys.stdlib_module_names:
        return root not in FORBIDDEN_IMPORT_ROOTS
    if name == "backend.runtime_evidence" or name.startswith(
            "backend.runtime_evidence."):
        return True
    if name == "backend.code_graph":
        return True
    if name.startswith("backend.code_graph."):
        return name[len("backend.code_graph."):] in _PUBLIC_CODE_GRAPH_NAMES
    return False


def _forbidden_calls(tree):
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom):
            base = _import_base(node, "backend.runtime_evidence.audit")
            for item in node.names:
                if item.name != "*":
                    aliases[item.asname or item.name] = (
                        f"{base}.{item.name}".strip("."))
    forbidden_io = {
        "open", "read_text", "read_bytes", "write_text", "write_bytes",
        "iterdir", "glob", "rglob", "resolve", "stat", "scandir", "listdir",
        "walk", "run", "Popen", "popen", "call", "check_call", "check_output",
        "system", "socket", "connect", "create_connection", "urlopen",
        "import_module", "__import__", "eval", "exec", "compile",
        "remove", "unlink", "rename", "mkdir", "makedirs", "rmdir",
        "chmod", "symlink", "link", "truncate", "fdopen",
    }
    unsafe = {"pickle.load", "pickle.loads", "marshal.load", "marshal.loads",
              "yaml.load", "yaml.load_all", "yaml.unsafe_load",
              "jsonpickle.decode"}
    forbidden_replace = {
        "os.replace", "pathlib.Path.replace", "pathlib.PosixPath.replace",
        "pathlib.WindowsPath.replace", "pathlib.PurePath.replace",
        "pathlib.PurePosixPath.replace", "pathlib.PureWindowsPath.replace",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts = []
        current = node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        dotted = ".".join(reversed(parts))
        head = dotted.split(".", 1)[0]
        if head in aliases:
            dotted = aliases[head] + dotted[len(head):]
        leaf = dotted.rsplit(".", 1)[-1]
        if dotted in unsafe or dotted in forbidden_replace or dotted in {
                "builtins.eval", "builtins.exec", "builtins.compile"}:
            yield dotted
        elif leaf in forbidden_io and dotted not in {"re.compile"}:
            yield dotted


def _audit_sources(root):
    """Static, name-based regression guard for the production boundary.

    This is not a sandbox and does not attempt to resolve deliberate
    ``getattr`` or dynamic-loader obfuscation. The runtime no-I/O guard covers
    actual ingestion behavior.
    """
    violations = []
    audited = []
    package = root / "backend" / "runtime_evidence"
    for source_path in sorted(package.rglob("*.py")):
        relative = source_path.relative_to(root)
        audited.append(relative.as_posix())
        module_name = _module_name_for(source_path, root)
        tree = ast.parse(source_path.read_text(encoding="utf-8"),
                         filename=str(source_path))
        for imported in _imported_modules(tree, module_name):
            if not _is_allowed_module(imported):
                violations.append(f"{relative}: forbidden import {imported}")
        for call_name in _forbidden_calls(tree):
            violations.append(f"{relative}: forbidden call {call_name}")
    return violations, audited


EXPECTED_AUDITED = [
    "backend/runtime_evidence/__init__.py",
    "backend/runtime_evidence/association.py",
    "backend/runtime_evidence/config.py",
    "backend/runtime_evidence/drafts.py",
    "backend/runtime_evidence/exceptions.py",
    "backend/runtime_evidence/identities.py",
    "backend/runtime_evidence/indexes.py",
    "backend/runtime_evidence/ingestor.py",
    "backend/runtime_evidence/json_adapter.py",
    "backend/runtime_evidence/json_scanner.py",
    "backend/runtime_evidence/lines.py",
    "backend/runtime_evidence/models.py",
    "backend/runtime_evidence/source_references.py",
    "backend/runtime_evidence/stack_parser.py",
    "backend/runtime_evidence/text_adapter.py",
    "backend/runtime_evidence/validation.py",
]


def test_runtime_evidence_sources_stay_in_memory_and_within_dependencies():
    root = Path(__file__).resolve().parents[1]
    violations, audited = _audit_sources(root)
    assert audited == EXPECTED_AUDITED
    assert violations == []


def test_source_audit_detects_a_planted_violation(tmp_path):
    package = tmp_path / "backend" / "runtime_evidence"
    package.mkdir(parents=True)
    (package / "bad.py").write_text(
        "import subprocess\n\n"
        "def damage():\n"
        "    subprocess.run(['git', 'status'])\n",
        encoding="utf-8")
    violations, audited = _audit_sources(tmp_path)
    assert audited == ["backend/runtime_evidence/bad.py"]
    assert any("forbidden import subprocess" in item for item in violations)
    assert any("forbidden call subprocess.run" in item for item in violations)


def test_source_audit_rejects_nonpublic_modules_and_bare_execution(tmp_path):
    package = tmp_path / "backend" / "runtime_evidence"
    package.mkdir(parents=True)
    (package / "bad.py").write_text(
        "import backend.code_graph.models\n"
        "import backend.code_parser.models\n\n"
        "def damage():\n"
        "    compile('1 + 1')\n"
        "    eval('1 + 1')\n",
        encoding="utf-8")
    violations, audited = _audit_sources(tmp_path)
    assert audited == ["backend/runtime_evidence/bad.py"]
    assert any(
        "forbidden import backend.code_graph.models" in item
        for item in violations)
    assert any(
        "forbidden import backend.code_parser.models" in item
        for item in violations)
    assert any("forbidden call compile" in item for item in violations)
    assert any("forbidden call eval" in item for item in violations)


def test_source_audit_classifies_absolute_from_imports(tmp_path):
    package = tmp_path / "backend" / "runtime_evidence"
    package.mkdir(parents=True)
    (package / "imports.py").write_text(
        "from backend import code_graph\n"
        "from backend import code_parser\n"
        "from backend.code_graph import models\n"
        "from backend.code_graph.models import GraphNode\n"
        "from backend.code_graph import (\n"
        "    CodeGraphInventory, GraphNode, GraphNodeKind)\n",
        encoding="utf-8")
    violations, audited = _audit_sources(tmp_path)
    assert audited == ["backend/runtime_evidence/imports.py"]
    assert [item.split(": ", 1)[1] for item in violations] == [
        "forbidden import backend.code_parser",
        "forbidden import backend.code_graph.models",
        "forbidden import backend.code_graph.models.GraphNode",
    ]


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "import subprocess as sp\n\n"
            "def damage():\n"
            "    sp.run(['git', 'status'])\n",
            [
                "forbidden import subprocess",
                "forbidden call subprocess.run",
            ],
        ),
        (
            "from subprocess import run as shell\n\n"
            "def damage():\n"
            "    shell(['git', 'status'])\n",
            [
                "forbidden import subprocess.run",
                "forbidden call subprocess.run",
            ],
        ),
    ),
    ids=("import-alias", "from-import-alias"),
)
def test_source_audit_rejects_forbidden_aliases(tmp_path, source, expected):
    package = tmp_path / "backend" / "runtime_evidence"
    package.mkdir(parents=True)
    (package / "aliased.py").write_text(source, encoding="utf-8")
    violations, audited = _audit_sources(tmp_path)
    assert audited == ["backend/runtime_evidence/aliased.py"]
    assert [item.split(": ", 1)[1] for item in violations] == expected


def test_source_audit_allows_fixed_re_compile(tmp_path):
    package = tmp_path / "backend" / "runtime_evidence"
    package.mkdir(parents=True)
    (package / "good.py").write_text(
        "import re\n\n"
        "_PATTERN = re.compile(r'^[a-z]+$')\n\n"
        "def match(value):\n"
        "    return _PATTERN.fullmatch(value)\n",
        encoding="utf-8")
    violations, audited = _audit_sources(tmp_path)
    assert audited == ["backend/runtime_evidence/good.py"]
    assert violations == []
