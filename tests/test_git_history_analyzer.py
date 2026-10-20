"""Tests for Module 7 analyzer."""

import pytest

from backend.git_history import analyzer as analyzer_module
from backend.git_history.analyzer import (
    GitHistoryAnalyzer,
    build_changes_by_commit,
    build_changes_by_path,
    build_co_change_index,
    build_commits_by_id,
    build_commits_touching_path,
    touched_paths,
)
from backend.git_history.exceptions import (
    GitConfigurationError,
    GitOutputTooLargeError,
    GitRevisionError,
)
from backend.git_history.models import GitChangeType, GitCommit, GitFileChange, GitIncompleteReason


def _change(**overrides):
    kwargs = dict(
        change_id="0" * 64,
        commit_id="a" * 40,
        change_type=GitChangeType.MODIFIED,
        old_path=None,
        new_path="x.py",
    )
    kwargs.update(overrides)
    # recompute change_id via model
    return GitFileChange(**kwargs)


def _commits(ids):
    out = []
    for cid in ids:
        out.append(GitCommit(
            commit_id=cid,
            parent_ids=(),
            author_name="A",
            author_email="a@example.com",
            author_timestamp=1,
            author_timezone="+00:00",
            committer_name="C",
            committer_email="c@example.com",
            committer_timestamp=1,
            committer_timezone="+00:00",
            message="msg",
            is_merge=False,
            is_root=True,
        ))
    return tuple(out)


# Task 17
def test_touched_paths_excludes_copy_source():
    copy = _change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                   old_path="src.py", new_path="dst.py")
    assert touched_paths("a" * 40, [copy]) == ("dst.py",)


def test_touched_paths_includes_both_paths_for_a_rename():
    rename = _change(commit_id="a" * 40, change_type=GitChangeType.RENAMED,
                     old_path="old.py", new_path="new.py")
    assert touched_paths("a" * 40, [rename]) == ("new.py", "old.py")


def test_changes_by_path_includes_copy_source():
    copy = _change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                   old_path="src.py", new_path="dst.py")
    index = build_changes_by_path([copy], {})
    assert set(index) == {"src.py", "dst.py"}


def test_commits_touching_path_excludes_copy_source():
    copy = _change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                   old_path="src.py", new_path="dst.py")
    index = build_commits_touching_path([copy])
    assert "src.py" not in index
    assert index["dst.py"] == ("a" * 40,)


def test_indexes_are_sorted_and_deduplicated():
    changes = [_change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
               _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py")]
    index = build_commits_touching_path(changes)
    assert index["x.py"] == ("a" * 40, "b" * 40)


def test_changes_by_commit_has_an_entry_for_every_commit():
    commits = _commits(["a" * 40, "b" * 40])
    index = build_changes_by_commit(commits, [])
    assert set(index) == {"a" * 40, "b" * 40}
    assert index["a" * 40] == ()


def test_changes_within_a_commit_are_sorted_by_type_then_path():
    changes = [
        _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="z.py"),
        _change(commit_id="a" * 40, change_type=GitChangeType.ADDED, new_path="b.py"),
        _change(commit_id="a" * 40, change_type=GitChangeType.ADDED, new_path="a.py"),
    ]
    index = build_changes_by_commit(_commits(["a" * 40]), changes)
    assert [(c.change_type.value, c.new_path) for c in index["a" * 40]] == [
        ("added", "a.py"), ("added", "b.py"), ("modified", "z.py")]


def test_changes_by_path_falls_back_when_the_commit_is_unknown():
    change = _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py")
    index = build_changes_by_path([change], {})
    assert index["x.py"] == (change,)


# Task 18
def test_pair_is_counted_once_across_commits():
    changes = [
        _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
        _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py"),
        _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
        _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py"),
    ]
    commits = _commits(["a" * 40, "b" * 40])
    # adjust timestamps for newest-first? _commits all ts=1; sort by commit_id asc tiebreak. Fine.
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc)
    assert result.co_changed_paths["x.py"] == ("y.py",)
    assert result.co_changed_paths["y.py"] == ("x.py",)


def test_index_is_symmetric():
    changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
               _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py")]
    commits = _commits(["a" * 40])
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc)
    assert result.co_changed_paths
    for left, partners in result.co_changed_paths.items():
        for right in partners:
            assert left in result.co_changed_paths[right]


def test_budget_overflow_omits_the_commit_atomically():
    changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
               _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py")]
    commits = _commits(["a" * 40])
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc, global_pair_budget=0)
    assert result.limit_exceeded is True
    assert result.co_changed_paths == {}
    assert set(result.incomplete_paths) == {"x.py", "y.py"}


def test_budget_counts_new_pairs_only():
    changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
               _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py"),
               _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="x.py"),
               _change(commit_id="b" * 40, change_type=GitChangeType.MODIFIED, new_path="y.py")]
    commits = _commits(["a" * 40, "b" * 40])
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc, global_pair_budget=1)
    assert result.limit_exceeded is False
    assert result.co_changed_paths["x.py"] == ("y.py",)


def test_per_commit_path_cap_omits_the_commit():
    paths = [f"f{i}.py" for i in range(5)]
    changes = [_change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path=p) for p in paths]
    commits = _commits(["a" * 40])
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc, per_commit_path_cap=3)
    assert result.limit_exceeded is True
    assert result.co_changed_paths == {}
    assert set(result.incomplete_paths) == set(paths)


def test_budget_exhaustion_across_many_commits_keeps_earlier_pairs():
    changes = []
    for i in range(3):
        for j in range(3):
            changes.append(_change(commit_id=f"{i}" * 40,
                                   change_type=GitChangeType.MODIFIED,
                                   new_path=f"f{i}_{j}.py"))
    commits = _commits([f"{i}" * 40 for i in range(3)])
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc, global_pair_budget=5)
    assert result.limit_exceeded is True
    assert result.co_changed_paths["f0_0.py"] == ("f0_1.py", "f0_2.py")
    assert "f1_0.py" not in result.co_changed_paths
    assert set(result.incomplete_paths) >= {f"f1_{j}.py" for j in range(3)}


def test_copy_source_is_never_a_co_change_partner():
    changes = [_change(commit_id="a" * 40, change_type=GitChangeType.COPIED,
                       old_path="src.py", new_path="dst.py"),
               _change(commit_id="a" * 40, change_type=GitChangeType.MODIFIED, new_path="other.py")]
    commits = _commits(["a" * 40])
    bcc = build_changes_by_commit(commits, changes)
    result = build_co_change_index(commits, bcc)
    assert "src.py" not in result.co_changed_paths
    assert result.co_changed_paths["dst.py"] == ("other.py",)


def test_module_constants_hold_the_production_values():
    assert analyzer_module.CO_CHANGE_PAIR_BUDGET == 10_000_000
    assert analyzer_module.CO_CHANGE_PER_COMMIT_PATH_CAP == 1000


# Task 19 / analyzer orchestration
import subprocess
from pathlib import Path

# helper imports for git_repo tests already via fixture


def test_analyze_returns_commits_and_changes(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.commit({"a.txt": b"2\n", "b.txt": b"2\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert inv.commit_count == 2 and inv.change_count == 3
    assert inv.history_is_complete is True
    assert inv.incomplete_reasons == ()


def test_truncation_sets_max_commits_reached(git_repo):
    repo = git_repo()
    for i in range(3):
        repo.commit({"a.txt": f"{i}\n".encode()})
    inv = GitHistoryAnalyzer().analyze(repo.path, max_commits=2)
    assert GitIncompleteReason.MAX_COMMITS_REACHED in inv.incomplete_reasons
    assert inv.truncated_by_limit is True
    assert inv.history_is_complete is False


def test_path_filter_records_reason_and_keeps_full_changes(git_repo):
    repo = git_repo()
    repo.commit({"keep.txt": b"1\n", "other.txt": b"1\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path, path_filter=("keep.txt",))
    assert GitIncompleteReason.PATH_FILTER_APPLIED in inv.incomplete_reasons
    selected = inv.commits[0]
    paths = {c.new_path for c in inv.changes_by_commit[selected.commit_id]}
    assert paths == {"keep.txt", "other.txt"}


def test_shallow_history_reports_reasons_and_no_fabricated_changes(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.commit({"a.txt": b"2\n"})
    shallow = repo.shallow_clone(depth=1)
    inv = GitHistoryAnalyzer().analyze(shallow.path)
    assert GitIncompleteReason.SHALLOW_HISTORY in inv.incomplete_reasons
    assert GitIncompleteReason.PARENT_UNAVAILABLE in inv.incomplete_reasons
    assert inv.commits_with_unavailable_baseline == (shallow.head,)
    assert inv.shallow_boundary_commit_ids == (shallow.head,)
    assert inv.changes_by_commit[shallow.head] == ()


def test_linked_worktree_of_a_shallow_clone_shares_the_shallow_boundary(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.commit({"a.txt": b"2\n"})
    shallow = repo.shallow_clone(depth=1)
    worktree = shallow.linked_worktree("wt")
    inv = GitHistoryAnalyzer().analyze(worktree)
    assert GitIncompleteReason.SHALLOW_HISTORY in inv.incomplete_reasons
    assert inv.shallow_boundary_commit_ids == (shallow.head,)
    assert inv.commits_with_unavailable_baseline == (shallow.head,)


def test_merge_sets_merge_semantics_partial(git_repo):
    repo = git_repo()
    base = repo.commit({"a.txt": b"1\n"})
    repo.branch("side", base)
    repo.commit({"b.txt": b"2\n"})
    repo.checkout("side")
    repo.commit({"c.txt": b"3\n"})
    repo.checkout("main")
    repo.merge("side")
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert GitIncompleteReason.MERGE_SEMANTICS_PARTIAL in inv.incomplete_reasons


def test_merges_excluded_sets_reason(git_repo):
    repo = git_repo()
    base = repo.commit({"a.txt": b"1\n"})
    repo.branch("side", base)
    repo.commit({"b.txt": b"2\n"})
    repo.checkout("side")
    repo.commit({"c.txt": b"3\n"})
    repo.checkout("main")
    repo.merge("side")
    inv = GitHistoryAnalyzer().analyze(repo.path, include_merges=False)
    assert GitIncompleteReason.MERGES_EXCLUDED in inv.incomplete_reasons


def test_empty_repository_returns_empty_inventory(git_repo):
    repo = git_repo()
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert inv.commits == () and inv.changes == ()
    assert inv.history_is_complete is True


def test_detached_head_is_recorded_but_not_incomplete(git_repo):
    repo = git_repo()
    head = repo.commit({"a.txt": b"1\n"})
    repo.detach(head)
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert inv.detached_head is True
    assert GitIncompleteReason.SHALLOW_HISTORY not in inv.incomplete_reasons


def test_scope_records_the_analysis_parameters(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path, revision="HEAD",
                                     path_filter=("b.txt", "a.txt", "b.txt"),
                                     include_merges=False, detect_copies=True)
    assert inv.scope.revision == "HEAD"
    assert inv.scope.path_filter == ("a.txt", "b.txt")
    assert inv.scope.include_merges is False
    assert inv.scope.detect_copies is True
    assert inv.scope.include_commit_messages is True
    assert inv.git_version is not None and inv.git_version.startswith("git version")


def test_messages_can_be_suppressed(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"}, message="secret message")
    inv = GitHistoryAnalyzer().analyze(repo.path, include_commit_messages=False)
    assert inv.commits[0].message is None
    assert inv.commit_count == 1


def test_a_commit_with_metadata_does_not_record_missing_metadata(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert GitIncompleteReason.MISSING_METADATA not in inv.incomplete_reasons


def test_an_absent_header_records_missing_metadata(git_repo, monkeypatch):
    from backend.git_history import adapter as adapter_module
    from backend.git_history.parser import RawCommitObject

    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})

    original = adapter_module.GitAdapter.fetch_commit_objects

    def fake(self, repository_path, commit_ids):
        raw = original(self, repository_path, commit_ids)
        for cid in list(raw):
            r = raw[cid]
            raw[cid] = RawCommitObject(
                commit_id=r.commit_id,
                tree_id=r.tree_id,
                parent_ids=r.parent_ids,
                author_name="",
                author_email="",
                author_timestamp=0,
                author_timezone="+00:00",
                author_header_present=False,
                committer_name=r.committer_name,
                committer_email=r.committer_email,
                committer_timestamp=r.committer_timestamp,
                committer_timezone=r.committer_timezone,
                committer_header_present=r.committer_header_present,
                message_bytes=r.message_bytes,
                encoding=r.encoding,
            )
        return raw

    monkeypatch.setattr(adapter_module.GitAdapter, "fetch_commit_objects", fake)
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert GitIncompleteReason.MISSING_METADATA in inv.incomplete_reasons
    assert inv.commits[0].author_name == ""
    assert inv.commits[0].author_timestamp == 0
    assert inv.commits[0].author_timezone == "+00:00"


def test_empty_namespace_is_a_configuration_error(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    with pytest.raises(GitConfigurationError):
        GitHistoryAnalyzer().analyze(repo.path, repository_namespace="")


def test_invalid_max_commits_is_a_configuration_error(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    with pytest.raises(GitConfigurationError):
        GitHistoryAnalyzer().analyze(repo.path, max_commits=0)


def test_change_evidence_cap_is_enforced(monkeypatch, git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n", "b.txt": b"1\n"})
    monkeypatch.setattr(analyzer_module, "MAX_CHANGES", 1)
    with pytest.raises(GitOutputTooLargeError):
        GitHistoryAnalyzer().analyze(repo.path)


def test_genuinely_unchanged_commit_is_distinguishable_from_a_missing_baseline(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.commit_empty()
    inv = GitHistoryAnalyzer().analyze(repo.path)
    empty_commit = [c for c in inv.commits if c.commit_id == repo.head][0]
    assert inv.changes_by_commit[empty_commit.commit_id] == ()
    assert empty_commit.commit_id not in inv.commits_with_unavailable_baseline


def test_commits_with_identical_timestamps_are_ordered_deterministically(git_repo):
    repo = git_repo()
    first = repo.commit({"a.txt": b"1\n"})
    stamp = repo.head_timestamp()
    second = repo.commit_with_timestamp({"a.txt": b"2\n"}, stamp)
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert inv.commits[0].committer_timestamp == inv.commits[1].committer_timestamp
    assert [c.commit_id for c in inv.commits] == sorted([first, second])


def test_constructor_max_commits_precedence(git_repo):
    repo = git_repo()
    for i in range(3):
        repo.commit({"a.txt": f"{i}\n".encode()})
    # constructor sets 2
    analyzer = GitHistoryAnalyzer(max_commits=2)
    inv = analyzer.analyze(repo.path)
    assert inv.truncated_by_limit is True
    assert inv.commit_count == 2
    # per-call overrides to 3
    inv2 = analyzer.analyze(repo.path, max_commits=3)
    assert inv2.truncated_by_limit is False
    assert inv2.commit_count == 3
    # per-call None uses constructor
    inv3 = analyzer.analyze(repo.path, max_commits=None)
    assert inv3.commit_count == 2


def test_non_head_unborn_symbolic_ref_returns_the_empty_inventory(git_repo):
    repo = git_repo()
    # A genuine symbolic ref whose target is unborn.  A plain branch name
    # such as "main" is not itself a symbolic ref and must not be treated as
    # one merely because HEAD points at refs/heads/main.
    repo.symbolic_ref("refs/heads/alias", "refs/heads/main")
    inv = GitHistoryAnalyzer().analyze(repo.path, revision="refs/heads/alias")
    assert inv.commits == ()
    assert inv.changes == ()
    assert inv.commit_count == 0
    assert inv.change_count == 0
    assert inv.history_is_complete is True
    assert inv.incomplete_reasons == ()
    assert inv.scope.revision == "refs/heads/alias"


def test_plain_branch_names_are_not_treated_as_unborn_symbolic_refs(git_repo):
    repo = git_repo()
    repo.symbolic_ref("refs/heads/alias", "refs/heads/main")
    for revision in ("main", "refs/heads/main", "alias"):
        with pytest.raises(GitRevisionError):
            GitHistoryAnalyzer().analyze(repo.path, revision=revision)


def test_non_head_symbolic_ref_resolves_once_its_target_has_commits(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.symbolic_ref("refs/heads/alias", "refs/heads/main")
    inv = GitHistoryAnalyzer().analyze(repo.path, revision="refs/heads/alias")
    assert inv.commit_count == 1
    assert inv.commits[0].commit_id == repo.head


def test_invalid_revision_still_raises_after_unborn_handling(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    with pytest.raises(GitRevisionError):
        GitHistoryAnalyzer().analyze(repo.path, revision="no-such-revision")


def test_reusing_one_analyzer_keeps_per_analysis_resource_state(git_repo, monkeypatch):
    from backend.git_history import adapter as adapter_module

    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    instances = []
    original_init = adapter_module.GitAdapter.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        instances.append(self)

    monkeypatch.setattr(adapter_module.GitAdapter, "__init__", tracking_init)
    analyzer = GitHistoryAnalyzer()
    try:
        first = analyzer.analyze(repo.path)
        assert first.commit_count == 1
        assert len(instances) == 1
        first_adapter = instances[0]

        # Simulate a first analysis that exhausted both per-analysis bounds.
        # A cached adapter would immediately fail the second analysis.
        first_adapter._started_at -= adapter_module.AGGREGATE_TIMEOUT_SECONDS + 60
        first_adapter._cumulative_bytes = adapter_module.CUMULATIVE_OUTPUT_BUDGET_BYTES

        second = analyzer.analyze(repo.path)
        assert second.commit_count == 1
        assert len(instances) == 2
        second_adapter = instances[1]
        assert second_adapter is not first_adapter
        assert second_adapter._helper_dir != first_adapter._helper_dir
        assert second_adapter.cumulative_output_bytes < adapter_module.CUMULATIVE_OUTPUT_BUDGET_BYTES
    finally:
        for instance in instances:
            instance.close()
    assert all(not instance._helper_dir.exists() for instance in instances)


def test_failed_analysis_cleans_up_and_does_not_poison_reuse(git_repo, monkeypatch):
    from backend.git_history import adapter as adapter_module

    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    helper_dirs = []
    original_init = adapter_module.GitAdapter.__init__

    def tracking_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        helper_dirs.append(self._helper_dir)

    monkeypatch.setattr(adapter_module.GitAdapter, "__init__", tracking_init)
    analyzer = GitHistoryAnalyzer()

    def run_failure():
        with pytest.raises(GitRevisionError):
            analyzer.analyze(repo.path, revision="no-such-revision")

    run_failure()
    assert len(helper_dirs) == 1
    assert not helper_dirs[0].exists()

    inv = analyzer.analyze(repo.path)
    assert inv.commit_count == 1
    assert len(helper_dirs) == 2
    assert helper_dirs[1] != helper_dirs[0]
    assert not helper_dirs[1].exists()
