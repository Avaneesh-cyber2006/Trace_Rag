"""Tests for Module 7 scaling and determinism."""

import pytest

from backend.git_history import analyzer as analyzer_module
from backend.git_history import GitHistoryAnalyzer
from backend.git_history.analyzer import build_co_change_index, build_changes_by_commit
from backend.git_history.models import GitChangeType, GitIncompleteReason


def test_git_command_count_is_linear_in_commits(git_repo, monkeypatch):
    def count_for(n):
        repo = git_repo()
        for i in range(n):
            repo.commit({"a.txt": f"{i}\n".encode()})
        seen = []
        original = analyzer_module.adapter_module.GitAdapter.run_raw if hasattr(analyzer_module, "adapter_module") else None
        from backend.git_history import adapter as adapter_module2
        original = adapter_module2.GitAdapter.run_raw
        def spy(self, args, repository_path=None, **kw):
            seen.append(list(args))
            return original(self, args, repository_path, **kw)
        with monkeypatch.context() as m:
            m.setattr(adapter_module2.GitAdapter, "run_raw", spy)
            GitHistoryAnalyzer().analyze(repo.path)
        return seen
    seen = count_for(5)
    diff_calls = [a for a in seen if a and a[0] == "diff-tree"]
    rev_parse_tree_calls = [a for a in seen if a and a[0] == "rev-parse"
                            and any(x.endswith("^{tree}") for x in a)]
    assert len(diff_calls) == 5
    assert len(rev_parse_tree_calls) == 4
    bigger = count_for(10)
    assert len([a for a in bigger if a and a[0] == "diff-tree"]) == 10
    assert len([a for a in bigger if a and a[0] == "rev-parse"
                and any(x.endswith("^{tree}") for x in a)]) == 9


def test_command_count_does_not_grow_with_symbols(git_repo, monkeypatch):
    from backend.git_history import adapter as adapter_module2
    small = git_repo()
    small.commit({"a.py": b"def f():\n    pass\n"})
    large = git_repo()
    large.commit({"a.py": b"".join(f"def f{i}():\n    pass\n".encode() for i in range(50))})
    assert small.path != large.path

    def count(repo):
        seen = []
        original = adapter_module2.GitAdapter.run_raw
        def spy(self, args, repository_path=None, **kw):
            seen.append(list(args))
            return original(self, args, repository_path, **kw)
        with monkeypatch.context() as m:
            m.setattr(adapter_module2.GitAdapter, "run_raw", spy)
            GitHistoryAnalyzer().analyze(repo.path)
        return len(seen)

    assert count(small) == count(large)


def test_repeat_analysis_is_deterministic(git_repo):
    repo = git_repo()
    for i in range(4):
        repo.commit({"a.txt": f"{i}\n".encode(), "b.txt": f"{i}\n".encode()})
    first = GitHistoryAnalyzer().analyze(repo.path)
    second = GitHistoryAnalyzer().analyze(repo.path)
    assert first.commit_ids == second.commit_ids
    assert first.co_changed_paths == second.co_changed_paths
    assert first.changes == second.changes


def test_published_indexes_cannot_be_mutated(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path)
    with pytest.raises(TypeError):
        inv.co_changed_paths["x.py"] = ("y.py",)


def test_duplicate_pairs_do_not_grow_the_budget(monkeypatch, git_repo):
    repo = git_repo()
    for i in range(20):
        repo.commit({"a.txt": f"{i}\n".encode(), "b.txt": f"{i}\n".encode()})
    monkeypatch.setattr(analyzer_module, "CO_CHANGE_PAIR_BUDGET", 1)
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert inv.co_changed_paths["a.txt"] == ("b.txt",)
    assert GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED not in inv.incomplete_reasons
    assert inv.co_change_incomplete_paths == ()


def test_global_pair_budget_overflow_is_reported_atomically(monkeypatch, git_repo):
    repo = git_repo()
    repo.commit({f"f{i}.txt": f"{i}\n".encode() for i in range(5)})
    monkeypatch.setattr(analyzer_module, "CO_CHANGE_PAIR_BUDGET", 5)
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED in inv.incomplete_reasons
    assert inv.history_is_complete is False
    assert inv.co_changed_paths == {}
    assert set(inv.co_change_incomplete_paths) == {f"f{i}.txt" for i in range(5)}
    assert inv.change_count == 5
    assert inv.changes_by_path
    assert inv.changes_by_commit[inv.commits[0].commit_id]


def test_production_budget_constants_are_unchanged():
    assert analyzer_module.CO_CHANGE_PAIR_BUDGET == 10_000_000
    assert analyzer_module.CO_CHANGE_PER_COMMIT_PATH_CAP == 1000


def test_published_index_lookup_is_a_direct_hit(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n", "b.txt": b"1\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path)
    commit = inv.commits[0]
    assert inv.commits_by_id[commit.commit_id] is commit
    assert inv.commits_by_id[commit.commit_id] is inv.commits_by_id[commit.commit_id]
    assert type(inv.commits_by_id).__name__ == "mappingproxy"
    with pytest.raises(KeyError):
        inv.commits_by_id["no-such-commit"]
