"""Increment 8: bounded, read-only, explicitly unverified Git hints."""

from dataclasses import FrozenInstanceError, replace

import pytest

from backend.git_history import GitChangeType, GitIncompleteReason
from backend.git_history.exceptions import GitParseError
from backend.retrieval import (
    HistoricalContext, HistoryAnnotator, InvalidHybridEvidence,
    InvalidRetrievalQuery,
)
from test_git_history_models import _change, _commit, _inventory, _scope
from test_hybrid_orchestrator import NAMESPACE, record
from backend.retrieval.hybrid import HybridHit


def hit(path="src/a.py", *, rank=1, suffix="a"):
    r = record(suffix, "alpha cache", relative_path=path)
    return HybridHit(
        repository_namespace=NAMESPACE, chunk_id=r.chunk_id,
        content_hash=r.content_hash, relative_path=r.relative_path,
        language=r.language, chunk_kind=r.chunk_kind,
        symbol_kind=r.symbol_kind, qualified_name=r.qualified_name,
        parent_qualified_name=r.parent_qualified_name, content=r.content,
        score=0.1, rank=rank, lexical_rank=rank, semantic_rank=rank,
    )


def inventory(*, changes=(), commits=None, **kwargs):
    if commits is None:
        commits = (_commit(),)
    return _inventory(
        repository_namespace=NAMESPACE, commits=commits, changes=changes, **kwargs
    )


def test_exact_git_path_matched_and_historical_context_is_not_source_verified():
    commit = _commit()
    exact = _change(new_path="src/a.py")
    other = _change(new_path="lib/a.py")
    # change IDs are derived from (commit, type, old path, new path).
    context = HistoryAnnotator(NAMESPACE, inventory(changes=(exact, other))).annotate(
        (hit(), hit("src/b.py", rank=2, suffix="b"))
    )
    assert isinstance(context, HistoricalContext)
    assert context.source_revision_verified is False
    assert context.history_is_complete is True
    assert context.scope_revision == "HEAD"
    assert context.paths[0].relative_path == "src/a.py"
    assert context.paths[0].total_matching_changes == 1
    assert context.paths[0].changes[0].commit_id == commit.commit_id
    assert context.paths[0].changes[0].change_id == exact.change_id
    assert context.paths[1].changes == ()
    with pytest.raises(FrozenInstanceError):
        context.paths[0].relative_path = "forged"


def test_case_sensitive_paths_no_suffix_or_basename_matches():
    evidence = inventory(changes=(
        _change(new_path="SRC/a.py"),
        _change(new_path="lib/a.py"),
    ))
    ctx = HistoryAnnotator(NAMESPACE, evidence).annotate((hit(),))
    assert ctx.paths[0].total_matching_changes == 0


def test_rename_record_only_attaches_to_new_path_and_exposes_prior_path():
    change = _change(change_type=GitChangeType.RENAMED, old_path="old.py", new_path="src/a.py")
    ann = HistoryAnnotator(NAMESPACE, inventory(changes=(change,)))
    ctx = ann.annotate((hit(), hit("old.py", rank=2, suffix="b")))
    assert ctx.paths[0].changes[0].prior_path == "old.py"
    assert ctx.paths[0].changes[0].change_type == "renamed"
    assert ctx.paths[1].changes == ()


def test_stable_newest_first_order_and_bound():
    recent = _commit(commit_id="b" * 40, committer_timestamp=10)
    old = _commit(commit_id="a" * 40, committer_timestamp=2)
    a = _change(commit_id=old.commit_id, new_path="src/a.py")
    b = _change(commit_id=recent.commit_id, new_path="src/a.py")
    context = HistoryAnnotator(NAMESPACE, inventory(
        commits=(recent, old), changes=(a, b)), per_path=1,
    ).annotate((hit(),))
    assert context.paths[0].total_matching_changes == 2
    assert [c.commit_id for c in context.paths[0].changes] == [recent.commit_id]


def test_unique_paths_in_hit_order_not_alphabetical_order():
    ctx = HistoryAnnotator(NAMESPACE, inventory()).annotate((
        hit("z.py"), hit("a.py", rank=2, suffix="b"), hit("z.py", rank=3, suffix="c")
    ))
    assert [p.relative_path for p in ctx.paths] == ["z.py", "a.py"]


def test_incomplete_history_flags_and_path_filter_preserved_not_ignored():
    history = inventory(
        scope=_scope(path_filter=("src",)),
        history_is_complete=False,
        incomplete_reasons=(GitIncompleteReason.PATH_FILTER_APPLIED,),
    )
    ctx = HistoryAnnotator(NAMESPACE, history).annotate((hit(),))
    assert ctx.history_is_complete is False
    assert ctx.incomplete_reasons == (GitIncompleteReason.PATH_FILTER_APPLIED,)
    assert ctx.scope_path_filter == ("src",)
    assert ctx.paths[0].changes == ()


@pytest.mark.parametrize("bad", [None, object(), False, 0, 21, 1.0])
def test_invalid_policy_limits_fail_before_scan(bad):
    with pytest.raises(InvalidRetrievalQuery):
        HistoryAnnotator(NAMESPACE, inventory(), bad)


def test_wrong_namespace_fails_closed_even_if_paths_match():
    bad = _inventory(repository_namespace="other")
    with pytest.raises(InvalidHybridEvidence):
        HistoryAnnotator(NAMESPACE, bad)


def test_bogus_input_and_noncontiguous_rank_rejected():
    ann = HistoryAnnotator(NAMESPACE, inventory())
    with pytest.raises(InvalidRetrievalQuery):
        ann.annotate([hit()])
    with pytest.raises(InvalidHybridEvidence):
        ann.annotate((hit(rank=2),))
    with pytest.raises(InvalidHybridEvidence):
        ann.annotate((object(),))


def test_duplicate_changes_rejected_not_double_counted():
    ch = _change()
    with pytest.raises(InvalidHybridEvidence):
        HistoryAnnotator(NAMESPACE, inventory(changes=(ch, ch))).annotate((hit(),))


def test_unknown_commit_and_duplicate_commits_rejected():
    missing = _change(commit_id="b" * 40)
    # The Module 7 model already rejects a change referencing a missing commit.
    with pytest.raises(GitParseError):
        inventory(changes=(missing,))
    with pytest.raises(InvalidHybridEvidence):
        HistoryAnnotator(NAMESPACE, inventory(commits=(_commit(), _commit()))).annotate((hit(),))


def test_empty_retrieval_and_zero_history_allowed():
    ctx = HistoryAnnotator(NAMESPACE, inventory()).annotate(())
    assert ctx.paths == () and ctx.source_revision_verified is False
