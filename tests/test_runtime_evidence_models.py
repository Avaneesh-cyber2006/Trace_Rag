from backend.runtime_evidence.exceptions import (
    RuntimeEvidenceConfigurationError, RuntimeEvidenceError,
    RuntimeEvidenceGraphError, RuntimeEvidenceIngestionError,
    RuntimeEvidenceInvariantError, RuntimeEvidenceLimitError,
    RuntimeEvidenceValidationError,
)


def test_all_fatal_errors_descend_from_runtime_evidence_error():
    assert RuntimeEvidenceError.__bases__ == (Exception,)
    for cls in (RuntimeEvidenceConfigurationError, RuntimeEvidenceValidationError,
                RuntimeEvidenceLimitError, RuntimeEvidenceGraphError,
                RuntimeEvidenceInvariantError, RuntimeEvidenceIngestionError):
        assert cls.__bases__ == (RuntimeEvidenceError,), cls.__name__


def test_exception_message_is_exact_and_not_rewritten():
    err = RuntimeEvidenceGraphError("runtime evidence graph is invalid")
    assert str(err) == "runtime evidence graph is invalid"
    assert err.args == ("runtime evidence graph is invalid",)
