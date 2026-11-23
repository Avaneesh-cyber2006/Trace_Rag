"""Offline answer schema, citation trust and excerpt validation."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from backend.causal_reasoner import (
    InvalidReasonedAnswer, assemble_evidence, parse_answer,
    redact_evidence, validate_answer,
)
from backend.retrieval.hybrid import HybridHit

NS = "trace-rag/repo"


def hit(text: str = "def alpha(): return 5") -> HybridHit:
    return HybridHit(
        NS, sha256(("src/a.py" + text).encode()).hexdigest(),
        sha256(text.encode()).hexdigest(), "src/a.py", "python", "symbol",
        "function", "alpha", None, text, 0.1, 1, 1, 1,
    )


def setup(text="def alpha(): return 5"):
    raw = assemble_evidence(NS, "why?", (hit(text),))
    return raw, redact_evidence(raw)


def answer(citations=None, *, category="indexed_fact", excerpt=None, status="answered"):
    claim = {"text": "The indexed chunk defines alpha.",
             "category": category, "citations": ["S1"] if citations is None else citations}
    if excerpt is not None:
        claim["excerpt"] = excerpt
    return {
        "status": status,
        "overview": "An indexed source was found; causation is not proven.",
        "claims": [claim], "hypotheses": [],
        "limitations": ["No runtime behavior has been verified."],
    }


def test_valid_cited_source_answer_and_exact_quote():
    raw, prepared = setup()
    response = validate_answer(raw, prepared, answer(excerpt="return 5"))
    assert response.answer.claims[0].excerpt == "return 5"
    assert [ref.label for ref in response.citations] == ["S1"]
    assert response.citations[0].content == "def alpha(): return 5"


@pytest.mark.parametrize("invalid", [
    lambda a: {**a, "unknown": "surprise"},
    lambda a: {**a, "claims": [{**a["claims"][0], "category": "root_cause"}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "citations": ["S01"]}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "citations": ["S1", "S1"]}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "citations": ["S999"]}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "citations": ["H1"]}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "excerpt": "not present"}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "category": "graph_relation"}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "text": "<script>alert(1)</script>"}]},
    lambda a: {**a, "claims": [{**a["claims"][0], "text": "token=abcdefghi"}]},
    lambda a: {**a, "overview": ""},
    lambda a: {**a, "status": "verified_root_cause"},
])
def test_invalid_output_rejected_without_loosening_schema(invalid):
    raw, prepared = setup()
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, invalid(answer()))


def test_redaction_prevents_quoting_hidden_secret():
    raw, prepared = setup('API_KEY="supersecretvalue"\ndef alpha(): return 5')
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, answer(excerpt="supersecretvalue"))
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, answer(excerpt="[REDACTED]"))
    accepted = validate_answer(raw, prepared, answer(excerpt="return 5"))
    assert "supersecretvalue" not in accepted.citations[0].content


def test_changed_redacted_projection_cannot_be_substituted():
    raw, prepared = setup()
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, replace(prepared, items=()), answer())
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, replace(prepared, query_text="different"), answer())


def test_no_source_or_unverified_only_cannot_produce_answered_result():
    raw = assemble_evidence(NS, "why?", ())
    prepared = redact_evidence(raw)
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, answer())
    insufficient = {"status": "insufficient_evidence", "overview": "Not enough evidence.",
                    "claims": [], "hypotheses": [], "limitations": ["No indexed source."]}
    result = validate_answer(raw, prepared, insufficient)
    assert result.answer.status.value == "insufficient_evidence"
    assert result.citations == ()


def test_possible_hypothesis_needs_citation_and_gaps_are_bounded():
    raw, prepared = setup()
    candidate = answer()
    candidate["hypotheses"] = [{
        "summary": "An unrelated caller may trigger it.",
        "supporting_citations": ["S1"], "contradicting_citations": [],
        "missing_observations": ["Need a matching runtime trace."],
        "status": "possible",
    }]
    assert len(validate_answer(raw, prepared, candidate).answer.hypotheses) == 1
    candidate["hypotheses"][0]["supporting_citations"] = []
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, candidate)


def test_duplicate_json_members_nonfinite_and_invalid_json_rejected():
    raw, prepared = setup()
    good = json.dumps(answer())
    assert parse_answer(good).status.value == "answered"
    for invalid in (
        '{"status":"answered","status":"insufficient_evidence"}',
        '{"status":NaN}',
        '{"status":Infinity}',
        'not json',
        '[]',
        '"foo"',
        json.dumps(answer()) + ' garbage',
    ):
        with pytest.raises(InvalidReasonedAnswer):
            validate_answer(raw, prepared, invalid)


def test_direct_model_object_does_not_bypass_parsing():
    raw, prepared = setup()
    compiled = parse_answer(answer())
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, compiled)


def test_arbitrary_line_ranges_and_numeric_confidence_cannot_be_invented():
    raw, prepared = setup()
    injected = answer()
    injected["claims"][0]["line_start"] = 10
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, injected)
    injected = answer()
    injected["confidence"] = 0.98
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, injected)


def test_graph_history_and_runtime_must_keep_their_separate_trust_categories():
    from backend.causal_reasoner import EvidenceItem, EvidenceKind
    raw, _ = setup()
    extra = (
        EvidenceItem("G1", EvidenceKind.REVISION_BOUND_GRAPH, NS, "src/a.py",
                     source_label="S1", details=(("seed", "S1"), ("edge_id", "a" * 64)),
                     source_revision_verified=True),
        EvidenceItem("H1", EvidenceKind.UNVERIFIED_GIT_HISTORY, NS, "src/a.py",
                     source_label="S1", details=(("commit_id", "a" * 40),)),
        EvidenceItem("R1", EvidenceKind.UNVERIFIED_RUNTIME, NS, "src/a.py",
                     source_label="S1", details=(("event_id", "b" * 64),)),
    )
    raw = replace(raw, items=raw.items + extra)
    prepared = redact_evidence(raw)
    assert [item.label for item in prepared.items] == ["S1", "G1", "H1", "R1"]
    model = answer()
    model["claims"].extend([
        {"text": "Static graph inventory reports a relationship.",
         "category": "graph_relation", "citations": ["G1"]},
        {"text": "Git inventory reports a path change.",
         "category": "reported_history", "citations": ["H1"]},
        {"text": "Capture inventory reports a frame.",
         "category": "reported_runtime", "citations": ["R1"]},
    ])
    checked = validate_answer(raw, prepared, model)
    assert [item.label for item in checked.citations] == ["S1", "G1", "H1", "R1"]
    model["claims"][1]["citations"] = ["H1"]
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, model)


def test_history_and_runtime_only_are_not_source_proof():
    from backend.causal_reasoner import EvidenceItem, EvidenceKind
    raw, _ = setup()
    raw = replace(raw, items=raw.items + (
        EvidenceItem("H1", EvidenceKind.UNVERIFIED_GIT_HISTORY, NS, "src/a.py",
                     source_label="S1"),
    ))
    prepared = redact_evidence(raw)
    history_only = answer(citations=["H1"], category="reported_history")
    with pytest.raises(InvalidReasonedAnswer):
        validate_answer(raw, prepared, history_only)
