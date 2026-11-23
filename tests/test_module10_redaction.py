"""Module 10 Increment 2: redaction stays offline and cannot authorize sending."""

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256

import pytest

from backend.causal_reasoner import (
    EvidenceBundle, EvidenceItem, EvidenceKind, EvidencePolicy,
    RedactionPolicy, UnsafeEvidenceForModel, assemble_evidence, redact_evidence,
)
from backend.retrieval.hybrid import HybridHit

NS = "trace-rag/repo"


def hit(text: str, rank: int = 1, path: str = "src/source.py") -> HybridHit:
    return HybridHit(
        NS, sha256((path + text).encode()).hexdigest(), sha256(text.encode()).hexdigest(),
        path, "python", "symbol", "function", "f", None, text,
        0.1, rank, rank, rank,
    )


def bundle(content: str = "def f(): return 1") -> EvidenceBundle:
    return assemble_evidence(NS, "why?", (hit(content),))


def test_plain_source_preserved_but_still_not_provider_authorized():
    raw = bundle()
    result = redact_evidence(raw)
    assert result.resolve("S1").content == "def f(): return 1"
    assert result.resolve("S1").kind is EvidenceKind.INDEXED_SOURCE
    assert result.provider_safe is False
    assert raw.provider_safe is False
    assert "src/source.py" not in repr(result)
    assert "def f()" not in repr(result)
    assert len(result.items) == 1
    with pytest.raises(FrozenInstanceError):
        result.items = ()


@pytest.mark.parametrize("sensitive", [
    'API_KEY="supersecretvalue"',
    'DB_PASSWORD = "topsecretvalue"',
    '"token": "abcdef0123456789"',
    'Authorization: Bearer abcdef0123456789abcdef',
    'MY_SECRET_KEY=supersecretvalue',
    'ghp_' + 'a' * 26,
    'sk-' + 'a' * 24,
    'contact: admin@example.org',
])
def test_known_secrets_redacted_without_changing_raw_registry(sensitive):
    raw = bundle("def f():\n    " + sensitive + "\n    return 1")
    prepared = redact_evidence(raw)
    assert sensitive not in prepared.resolve("S1").content
    assert "[REDACTED]" in prepared.resolve("S1").content
    assert "sensitive_text_redacted" in prepared.omissions
    assert sensitive in raw.resolve("S1").content
    assert prepared.resolve("S1").redacted is True


def test_private_key_source_is_omitted_not_truncated_or_sent():
    raw = bundle("-----BEGIN PRIVATE KEY-----\nSUPERSECRET")
    prepared = redact_evidence(raw)
    assert prepared.items == ()
    assert "privacy_item_omitted" in prepared.omissions


def test_query_secret_cannot_be_silently_changed():
    raw = assemble_evidence(NS, 'why? api_key=abcde12345', (hit("safe text"),))
    with pytest.raises(UnsafeEvidenceForModel):
        redact_evidence(raw)


def test_budget_drops_complete_item_deterministically():
    raw = bundle("def f(): return 'a' * 5000")
    result = redact_evidence(raw, policy=RedactionPolicy(max_total_bytes=10))
    assert not result.items
    assert "redacted_byte_budget" in result.omissions


def test_malformed_source_identity_is_rejected_at_boundary():
    # Cannot build such an invalid string through Module 9; test defensive boundary.
    raw = bundle()
    forged = replace(raw.resolve("S1"), content="secret\ud800")
    with pytest.raises(UnsafeEvidenceForModel):
        redact_evidence(replace(raw, items=(forged,)))


def test_dangling_relationship_or_history_does_not_survive_source_omission():
    raw = bundle("-----BEGIN PRIVATE KEY-----")
    source = raw.items[0]
    history = EvidenceItem("H1", EvidenceKind.UNVERIFIED_GIT_HISTORY, NS,
                           "src/source.py", source_label="S1")
    redacted = redact_evidence(replace(raw, items=(source, history)))
    assert redacted.items == ()
    assert "dependent_evidence_omitted" in redacted.omissions


def test_bad_policy_and_duplicate_labels_fail_closed():
    raw = bundle()
    with pytest.raises(UnsafeEvidenceForModel):
        redact_evidence(raw, policy=RedactionPolicy(max_total_bytes=True))
    with pytest.raises(UnsafeEvidenceForModel):
        redact_evidence(replace(raw, items=raw.items * 2))
