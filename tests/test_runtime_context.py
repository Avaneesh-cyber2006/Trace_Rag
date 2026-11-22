"""Increment 9: bounded, sanitized, unverified runtime path clues."""

from dataclasses import FrozenInstanceError

import pytest

from backend.runtime_evidence import (
    RuntimeArtifact, RuntimeArtifactFormat, RuntimeEvidenceRequest,
    RuntimeFrameLanguage, ingest_runtime_evidence,
)
from backend.runtime_evidence.models import PathMatchBasis
from backend.retrieval import RuntimeAnnotator, InvalidHybridEvidence, InvalidRetrievalQuery
from test_history_context import hit
from test_hybrid_orchestrator import NAMESPACE


def stack(path="src/a.py", line=17, *, name="trace.log"):
    data = (f'Traceback (most recent call last):\n'
            f'  File "{path}", line {line}, in run\n'
            f'ValueError: PRIVATE-ERROR-MESSAGE\n').encode()
    return RuntimeArtifact(name, RuntimeArtifactFormat.STACK_TRACE_TEXT,
                           data, RuntimeFrameLanguage.PYTHON)


def inventory(*artifacts, namespace=NAMESPACE, declared_revision=None):
    return ingest_runtime_evidence(RuntimeEvidenceRequest(
        namespace, artifacts, declared_revision=declared_revision,
    ))


def test_exact_paths_only_are_annotated_and_unverified():
    inv = inventory(stack("src/a.py"), stack("lib/a.py", name="other.log"))
    context = RuntimeAnnotator(NAMESPACE, inv).annotate((hit(),))
    assert len(context.paths) == 1
    assert context.paths[0].relative_path == "src/a.py"
    assert context.paths[0].total_matching_frames == 1
    clue = context.paths[0].frames[0]
    assert clue.frame_id == next(f.frame_id for f in inv.frames if f.relative_path == "src/a.py")
    assert clue.path_match_basis is PathMatchBasis.UNVERIFIED_RELATIVE_PATH
    assert clue.line == 17
    assert context.source_revision_verified is False
    assert context.causal_relationship_verified is False


def test_no_basename_or_casefold_matching():
    inv = inventory(stack("lib/a.py"), stack("SRC/a.py", name="other.log"))
    ctx = RuntimeAnnotator(NAMESPACE, inv).annotate((hit(),))
    assert ctx.paths[0].frames == ()
    assert ctx.paths[0].total_matching_frames == 0


def test_order_unique_paths_and_bounded_count():
    inv = inventory(*(stack("src/a.py", i, name=f"t{i:02}.log") for i in range(1, 11)))
    ctx = RuntimeAnnotator(NAMESPACE, inv, per_path=2).annotate((
        hit("src/b.py"), hit("src/a.py", rank=2, suffix="a"),
        hit("src/a.py", rank=3, suffix="c"),
    ))
    assert [x.relative_path for x in ctx.paths] == ["src/b.py", "src/a.py"]
    assert ctx.paths[0].frames == ()
    assert ctx.paths[1].total_matching_frames == 10
    assert len(ctx.paths[1].frames) == 2
    assert [x.line for x in ctx.paths[1].frames] == [1, 2]


def test_declared_revision_is_not_source_verified():
    inv = inventory(stack(), declared_revision="main HEAD claimed by operator")
    ctx = RuntimeAnnotator(NAMESPACE, inv).annotate((hit(),))
    assert ctx.declared_revision == "main HEAD claimed by operator"
    assert ctx.source_revision_verified is False


def test_sensitive_runtime_text_not_copied_into_result_or_repr():
    inv = inventory(stack())
    context = RuntimeAnnotator(NAMESPACE, inv).annotate((hit(),))
    data = repr(context) + repr(context.paths) + repr(context.paths[0].frames)
    assert "PRIVATE-ERROR-MESSAGE" not in data
    assert not hasattr(context.paths[0].frames[0], "message")
    assert not hasattr(context.paths[0].frames[0], "trace_id")
    with pytest.raises(FrozenInstanceError):
        context.paths[0].relative_path = "new.py"


def test_empty_and_nonframe_events_stay_empty():
    artifact = RuntimeArtifact("log.txt", RuntimeArtifactFormat.TEXT_LOG,
                               b"SECRET LOG WITHOUT SOURCE LOCATION")
    inv = inventory(artifact)
    ctx = RuntimeAnnotator(NAMESPACE, inv).annotate((hit(),))
    assert ctx.paths[0].total_matching_frames == 0
    assert ctx.ingestion_complete is True


@pytest.mark.parametrize("bad", [None, object(), False, 0, 21, 1.0])
def test_invalid_limits_fail_closed(bad):
    with pytest.raises(InvalidRetrievalQuery):
        RuntimeAnnotator(NAMESPACE, inventory(stack()), bad)


def test_mismatched_namespace_fails_closed():
    with pytest.raises(InvalidHybridEvidence, match="Runtime evidence is invalid"):
        RuntimeAnnotator(NAMESPACE, inventory(stack(), namespace="different"))


def test_invalid_hits_and_noncontiguous_ranks():
    annotator = RuntimeAnnotator(NAMESPACE, inventory(stack()))
    with pytest.raises(InvalidRetrievalQuery):
        annotator.annotate([hit()])
    with pytest.raises(InvalidHybridEvidence):
        annotator.annotate((hit(rank=2),))
    with pytest.raises(InvalidHybridEvidence):
        annotator.annotate((object(),))


def test_tampered_inventory_revalidated_on_entry():
    inv = inventory(stack())
    object.__setattr__(inv, "total_frames", 1_000)
    with pytest.raises(InvalidHybridEvidence, match="Runtime evidence is invalid"):
        RuntimeAnnotator(NAMESPACE, inv)


def test_empty_hit_set_has_no_path_clues():
    ctx = RuntimeAnnotator(NAMESPACE, inventory(stack())).annotate(())
    assert ctx.paths == () and ctx.source_revision_verified is False
