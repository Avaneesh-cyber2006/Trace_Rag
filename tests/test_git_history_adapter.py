"""Tests for Module 7 adapter."""

import io
import subprocess
import sys
from pathlib import Path

import pytest

from backend.git_history import adapter as adapter_module
from backend.git_history.adapter import (
    GitAdapter,
    build_git_environment,
    terminate_process,
)
from backend.git_history.exceptions import (
    GitCommandError,
    GitCommandTimeoutError,
    GitConfigurationError,
    GitOutputTooLargeError,
    GitParseError,
    GitRepositoryError,
    GitRevisionError,
    GitUnavailableError,
)
from backend.git_history.invariants import EMPTY_TREE_SHA1
from backend.git_history.models import GitChangeType
from backend.git_history.parser import parse_batch_stream


def adapter() -> GitAdapter:
    return GitAdapter()


def _repo(tmp_path: Path, git_repo) -> Path:
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    return repo.path


def _big_repo(tmp_path: Path, git_repo) -> Path:
    repo = git_repo()
    big_message = "x" * (70 * 1024)
    repo.commit({"a.txt": b"1\n"}, message=big_message)
    return repo.path


# Task 8
def test_environment_contains_no_inherited_git_variables(tmp_path):
    env = build_git_environment(Path(tmp_path))
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_ATTR_NOSYSTEM"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GIT_PAGER"] == "cat" and env["PAGER"] == "cat"
    assert env["GIT_EXTERNAL_DIFF"] == ""
    assert env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    assert "GIT_INDEX_FILE" not in env and "GIT_OBJECT_DIRECTORY" not in env
    assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" not in env
    assert "GIT_TRACE" not in env and "GIT_TRACE2" not in env
    assert "HTTP_PROXY" not in env and "ALL_PROXY" not in env


def test_environment_starts_from_empty_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "C:/leak")
    monkeypatch.setenv("HTTPS_PROXY", "http://leak")
    env = build_git_environment(Path(tmp_path))
    assert "HTTPS_PROXY" not in env
    assert "GIT_CONFIG_GLOBAL" in env
    config = Path(env["GIT_CONFIG_GLOBAL"])
    assert config.parent == Path(tmp_path)
    assert config.is_file() and config.stat().st_size == 0
    assert "leak" not in env["GIT_CONFIG_GLOBAL"]


def test_adapter_rejects_invalid_configuration():
    with pytest.raises(GitConfigurationError):
        GitAdapter(command_timeout_seconds=0)
    with pytest.raises(GitConfigurationError):
        GitAdapter(max_output_bytes=0)
    with pytest.raises(GitConfigurationError):
        GitAdapter(git_executable="")


def test_missing_git_executable_raises_git_unavailable(tmp_path):
    adapter = GitAdapter(git_executable="git-does-not-exist-xyz")
    with pytest.raises(GitUnavailableError):
        adapter.run(["--version"], tmp_path)


def test_minimum_git_version_constant_holds_the_production_value():
    assert adapter_module.MINIMUM_GIT_VERSION == (2, 29)


def test_run_raw_exposes_the_exit_status_without_raising(tmp_path):
    adapter = GitAdapter()
    result = adapter.run_raw(["rev-parse", "--verify", "--end-of-options", "no-such-rev"], tmp_path)
    assert result.returncode == 128
    assert isinstance(result.stdout, bytes) and isinstance(result.stderr, bytes)


# Task 9
def test_oversized_output_is_rejected(tmp_path, git_repo):
    adapter = GitAdapter(max_output_bytes=1024)
    with pytest.raises(GitOutputTooLargeError):
        adapter.run(["cat-file", "-p", "HEAD"], _big_repo(tmp_path, git_repo))


def test_output_within_cap_is_returned(tmp_path, git_repo):
    adapter = GitAdapter()
    out = adapter.run(["rev-parse", "HEAD"], _repo(tmp_path, git_repo))
    assert len(out.strip()) == 40


def test_child_process_is_reaped_after_failure(tmp_path, git_repo, monkeypatch):
    adapter = GitAdapter(max_output_bytes=16)
    spawned = []
    original_popen = adapter_module.subprocess.Popen
    def spy(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        spawned.append(proc)
        return proc
    monkeypatch.setattr(adapter_module.subprocess, "Popen", spy)
    with pytest.raises(GitOutputTooLargeError):
        adapter.run(["cat-file", "-p", "HEAD"], _big_repo(tmp_path, git_repo))
    assert spawned
    for proc in spawned:
        assert proc.returncode is not None


def test_large_stdout_is_drained_without_deadlock(tmp_path, git_repo):
    adapter = GitAdapter(max_output_bytes=50 * 1024 * 1024)
    out = adapter.run(["cat-file", "-p", "HEAD"], _big_repo(tmp_path, git_repo))
    assert len(out) > 64 * 1024


def test_combined_stdout_stderr_share_one_cap(tmp_path, git_repo, monkeypatch):
    import io

    class _CombinedChild:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.returncode = 0
            self.stdin = None
            self.stdout = io.BytesIO(b"x" * 600)
            self.stderr = io.BytesIO(b"y" * 600)
            self.args = ["git"]
            self.pid = 1234
        def wait(self, timeout=None):
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -15
        def kill(self):
            self.killed = True
            self.returncode = -9
        def poll(self):
            return self.returncode

    child = _CombinedChild()
    monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: child)
    adapter = GitAdapter(max_output_bytes=1024)
    with pytest.raises(GitOutputTooLargeError):
        adapter.run(["rev-parse", "HEAD"], tmp_path)
    assert child.terminated or child.killed


def test_per_command_timeout_raises_and_reaps_the_child(tmp_path, git_repo, monkeypatch):
    import io

    class _TimeoutChild:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.returncode = None
            self.stdin = None
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.args = ["git"]
            self.pid = 1234
            self._timed_out = False
        def wait(self, timeout=None):
            if timeout is not None and not self._timed_out:
                self._timed_out = True
                raise subprocess.TimeoutExpired(cmd="git", timeout=timeout)
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -15
        def kill(self):
            self.killed = True
            self.returncode = -9
        def poll(self):
            return self.returncode

    repo_path = _repo(tmp_path, git_repo)
    child = _TimeoutChild()
    monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: child)
    adapter = GitAdapter(command_timeout_seconds=0.5)
    with pytest.raises(GitCommandTimeoutError):
        adapter.run(["rev-parse", "HEAD"], repo_path)
    assert child.terminated or child.killed
    assert child.returncode is not None


# Task 10
def test_cumulative_budget_blocks_many_small_commands(monkeypatch, tmp_path, git_repo):
    monkeypatch.setattr(adapter_module, "CUMULATIVE_OUTPUT_BUDGET_BYTES", 4096)
    adapter = GitAdapter(max_output_bytes=50 * 1024 * 1024)
    repo = _repo(tmp_path, git_repo)
    adapter.run(["rev-parse", "HEAD"], repo)
    with pytest.raises(GitOutputTooLargeError):
        for _ in range(200):
            adapter.run(["rev-parse", "HEAD"], repo)


def test_cumulative_counter_accumulates_across_commands():
    adapter = GitAdapter()
    adapter.consume_output_budget(10)
    adapter.consume_output_budget(20)
    assert adapter.cumulative_output_bytes == 30


def test_aggregate_deadline_is_enforced(monkeypatch):
    monkeypatch.setattr(adapter_module, "AGGREGATE_TIMEOUT_SECONDS", -1.0)
    adapter = GitAdapter()
    with pytest.raises(GitCommandTimeoutError):
        adapter.check_aggregate_deadline()


def test_command_timeout_is_capped_by_remaining_aggregate_time(monkeypatch, tmp_path):
    adapter = GitAdapter(command_timeout_seconds=30.0)
    started = adapter._started_at
    monkeypatch.setattr(adapter_module.time, "monotonic",
                        lambda: started + adapter_module.AGGREGATE_TIMEOUT_SECONDS - 0.25)
    seen = {}

    class _RecordingChild:
        def __init__(self):
            self.terminated = False
            self.killed = False
            self.returncode = None
            self.stdin = None
            self.stdout = __import__("io").BytesIO()
            self.stderr = __import__("io").BytesIO()
            self.args = ["git"]
            self.pid = 1234
            self._timed_out = False
        def wait(self, timeout=None):
            if "timeout" not in seen:
                seen["timeout"] = timeout
            if timeout is not None and not self._timed_out and (timeout is None or timeout <= 0.25):
                self._timed_out = True
                raise __import__("subprocess").TimeoutExpired(cmd="git", timeout=timeout)
            return self.returncode
        def terminate(self):
            self.terminated = True
            self.returncode = -15
        def kill(self):
            self.killed = True
            self.returncode = -9
        def poll(self):
            return self.returncode

    child = _RecordingChild()
    monkeypatch.setattr(adapter_module.subprocess, "Popen", lambda *a, **k: child)
    with pytest.raises(GitCommandTimeoutError):
        adapter.run(["rev-parse", "HEAD"], tmp_path)
    assert seen.get("timeout") is not None
    assert seen["timeout"] <= 0.25


# Task 11
def test_inspect_repository_reports_non_bare_non_shallow(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"a\n"})
    ctx = adapter().inspect_repository(repo.path)
    assert ctx.is_bare is False and ctx.is_shallow is False
    assert ctx.git_version.startswith("git version")


def test_resolve_revision_returns_full_object_id(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"a\n"})
    assert len(adapter().resolve_revision(repo.path, "HEAD")) == 40


def test_invalid_revision_raises_git_revision_error(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"a\n"})
    with pytest.raises(GitRevisionError):
        adapter().resolve_revision(repo.path, "no-such-revision")


def test_revision_may_not_start_with_dash(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"a\n"})
    with pytest.raises(GitRevisionError):
        adapter().resolve_revision(repo.path, "--upload-pack=evil")


def test_detached_head_is_detected(git_repo):
    repo = git_repo()
    head = repo.commit({"a.txt": b"a\n"})
    repo.detach(head)
    assert adapter().is_detached_head(repo.path) is True


def test_unborn_head_is_reported(git_repo):
    repo = git_repo()
    assert repo.is_unborn() is True


def test_rev_parse_verify_never_receives_two_revision_arguments(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    adapter().resolve_revision(repo.path, "HEAD")
    assert seen
    for args in seen:
        if "rev-parse" in args and "--verify" in args:
            revisions = [a for a in args if a.endswith("^{commit}") or a.endswith("^{tree}")]
            assert len(revisions) <= 1, args


def test_linked_worktree_shares_the_common_dir(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    worktree = repo.linked_worktree("wt")
    ctx = adapter().inspect_repository(worktree)
    assert ctx.git_common_dir.resolve() == (repo.path / ".git").resolve()


def test_unborn_head_is_detected_by_the_adapter(git_repo):
    repo = git_repo()
    assert adapter().is_unborn_head(repo.path) is True


def test_an_old_git_version_is_rejected(monkeypatch):
    monkeypatch.setattr(adapter_module, "MINIMUM_GIT_VERSION", (99, 0))
    with pytest.raises(GitUnavailableError):
        adapter().git_version()


def test_the_running_git_version_is_accepted():
    version = adapter().git_version()
    assert version.startswith("git version ")


# Task 12
def test_enumeration_returns_oldest_first(git_repo):
    repo = git_repo()
    first = repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    ids = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                     include_merges=True, path_filter=()).commit_ids
    assert ids == (first, second)


def test_max_commits_boundary_is_exact(git_repo):
    repo = git_repo()
    first = repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    third = repo.commit({"a.txt": b"3\n"})
    exact = adapter().enumerate_commits(repo.path, "HEAD", max_commits=3,
                                       include_merges=True, path_filter=())
    assert len(exact.commit_ids) == 3 and exact.truncated is False
    assert set(exact.commit_ids) == {first, second, third}
    over = adapter().enumerate_commits(repo.path, "HEAD", max_commits=2,
                                      include_merges=True, path_filter=())
    assert len(over.commit_ids) == 2 and over.truncated is True
    assert set(over.commit_ids) == {second, third}


def test_include_merges_false_excludes_merge_commits(git_repo):
    repo = git_repo()
    base = repo.commit({"a.txt": b"1\n"})
    repo.branch("side", base)
    repo.commit({"b.txt": b"2\n"})
    repo.checkout("side")
    repo.commit({"c.txt": b"3\n"})
    repo.checkout("main")
    repo.merge("side")
    with_merges = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                             include_merges=True, path_filter=())
    without = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                         include_merges=False, path_filter=())
    assert len(without.commit_ids) == len(with_merges.commit_ids) - 1


def test_path_filter_selects_commits_only(git_repo):
    repo = git_repo()
    repo.commit({"keep.txt": b"1\n", "other.txt": b"1\n"})
    repo.commit({"other.txt": b"2\n"})
    selected = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                          include_merges=True,
                                          path_filter=("keep.txt",))
    assert len(selected.commit_ids) == 1


def test_path_filter_uses_literal_pathspec(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"lit[1].txt": b"1\n"})
    repo.commit({"lit1.txt": b"2\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    selected = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                          include_merges=True,
                                          path_filter=("lit[1].txt",))
    assert len(selected.commit_ids) == 1
    rev_list = [a for a in seen if a and a[0] == "rev-list"]
    assert rev_list
    assert any(arg.startswith(":(literal)") for arg in rev_list[0])


def test_invalid_path_filter_is_rejected(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    for bad in ("", "a\\b", "/abs", "../up", "./here", "a//b", "a\x00b"):
        with pytest.raises(GitParseError):
            adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                       include_merges=True, path_filter=(bad,))


@pytest.mark.skipif(sys.platform == "win32",
                    reason="'*' and '?' are illegal filename characters on Windows")
def test_glob_characters_in_a_path_filter_are_treated_literally(git_repo):
    repo = git_repo()
    repo.commit({"a*.txt": b"1\n"})
    repo.commit({"ab.txt": b"2\n"})
    selected = adapter().enumerate_commits(repo.path, "HEAD", max_commits=None,
                                          include_merges=True,
                                          path_filter=("a*.txt",))
    assert len(selected.commit_ids) == 1


# Task 13
def test_fetch_returns_exactly_the_requested_commits(git_repo):
    repo = git_repo()
    first = repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    objects = adapter().fetch_commit_objects(repo.path, [first, second])
    assert set(objects) == {first, second}
    assert objects[first].parent_ids == ()
    assert objects[second].parent_ids == (first,)


def test_shallow_boundary_keeps_real_parent_and_is_not_root(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.commit({"a.txt": b"2\n"})
    shallow = repo.shallow_clone(depth=1)
    objects = adapter().fetch_commit_objects(shallow.path, [shallow.head])
    boundary = objects[shallow.head]
    assert len(boundary.parent_ids) == 1
    assert boundary.parent_ids[0] != shallow.head


def test_fetch_handles_input_larger_than_pipe_capacity(git_repo):
    repo = git_repo()
    ids = []
    for i in range(2000):
        ids.append(repo.commit({"a.txt": f"{i}\n".encode()}))
    objects = adapter().fetch_commit_objects(repo.path, ids)
    assert set(objects) == set(ids)
    assert len(objects) == 2000


# Task 14
def test_root_commit_uses_the_empty_tree_without_a_command(git_repo, monkeypatch):
    repo = git_repo()
    root = repo.commit({"a.txt": b"1\n"})
    raw = adapter().fetch_commit_objects(repo.path, [root])[root]
    calls = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path, **kw):
        calls.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    result = adapter().resolve_baseline(repo.path, root, raw)
    assert result.unavailable is False
    assert result.baseline_tree == EMPTY_TREE_SHA1
    assert not any("rev-parse" in " ".join(a) for a in calls)


def test_non_root_commit_resolves_first_parent_tree_with_one_command(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    raw = adapter().fetch_commit_objects(repo.path, [second])[second]
    calls = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path, **kw):
        calls.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    result = adapter().resolve_baseline(repo.path, second, raw)
    assert result.unavailable is False
    rev_parse_calls = [c for c in calls if "rev-parse" in c]
    assert len(rev_parse_calls) == 1
    assert sum(1 for a in rev_parse_calls[0] if a.endswith("^1^{tree}")) == 1


def test_shallow_boundary_reports_an_unavailable_baseline(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.commit({"a.txt": b"2\n"})
    shallow = repo.shallow_clone(depth=1)
    raw = adapter().fetch_commit_objects(shallow.path, [shallow.head])[shallow.head]
    result = adapter().resolve_baseline(shallow.path, shallow.head, raw)
    assert result.unavailable is True
    assert result.baseline_tree is None


def test_first_parent_resolution_uses_exactly_one_revision_argument(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    raw = adapter().fetch_commit_objects(repo.path, [second])[second]
    adapter().resolve_baseline(repo.path, second, raw)
    assert seen
    for args in seen:
        if "rev-parse" in args and "--verify" in args:
            revisions = [a for a in args if a.endswith("^{tree}")]
            assert len(revisions) == 1, args


# Task 15
def test_root_commit_reports_every_file_as_added(git_repo):
    repo = git_repo()
    root = repo.commit({"a.txt": b"1\n", "b.txt": b"2\n"})
    changes = adapter().changes_for_commit(repo.path, root)
    assert {c.new_path for c in changes} == {"a.txt", "b.txt"}
    assert all(c.change_type is GitChangeType.ADDED for c in changes)


def test_linear_commit_reports_modification(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    changes = adapter().changes_for_commit(repo.path, second)
    assert [(c.change_type, c.new_path) for c in changes] == [(GitChangeType.MODIFIED, "a.txt")]


def test_merge_reports_only_first_parent_changes(git_repo):
    repo = git_repo()
    base = repo.commit({"a.txt": b"1\n"})
    repo.branch("side", base)
    repo.commit({"b.txt": b"2\n"})
    repo.checkout("side")
    repo.commit({"c.txt": b"3\n"})
    repo.checkout("main")
    merge = repo.merge("side")
    paths = {c.new_path for c in adapter().changes_for_commit(repo.path, merge)}
    assert "c.txt" in paths
    assert "b.txt" not in paths


def test_binary_file_is_reported_as_a_change(git_repo):
    repo = git_repo()
    repo.commit({"bin.dat": bytes(range(256))})
    repo.commit({"bin.dat": bytes(reversed(range(256)))})
    changes = adapter().changes_for_commit(repo.path, repo.head)
    assert [c.change_type for c in changes] == [GitChangeType.MODIFIED]
    assert changes[0].new_path == "bin.dat"


def test_deleted_file_is_reported_as_a_deletion(git_repo):
    repo = git_repo()
    repo.commit({"gone.txt": b"1\n", "kept.txt": b"1\n"})
    repo.delete("gone.txt")
    changes = adapter().changes_for_commit(repo.path, repo.head)
    assert [(c.change_type, c.old_path, c.new_path) for c in changes] == [
        (GitChangeType.DELETED, "gone.txt", None)]


def test_filename_with_spaces_and_unicode_is_extracted(git_repo):
    repo = git_repo()
    repo.commit({"a file with spaces.txt": b"1\n", "caf\u00e9.py": b"2\n"})
    changes = adapter().changes_for_commit(repo.path, repo.head)
    assert {c.new_path for c in changes} == {"a file with spaces.txt", "caf\u00e9.py"}


# Task 16
def test_flag_combinations():
    assert adapter().build_diff_flags(True, False) == ["--find-renames"]
    assert adapter().build_diff_flags(False, False) == []
    assert adapter().build_diff_flags(True, True) == ["--find-renames", "--find-copies-harder"]
    assert adapter().build_diff_flags(False, True) == ["--no-renames", "--find-copies-harder"]


def test_rename_is_reported_with_similarity(git_repo):
    repo = git_repo()
    repo.commit({"old.txt": b"line one\nline two\nline three\n"})
    renamed = repo.commit_rename("old.txt", "new.txt")
    changes = adapter().changes_for_commit(repo.path, renamed, detect_renames=True, detect_copies=False)
    assert changes[0].change_type is GitChangeType.RENAMED
    assert 0 <= changes[0].similarity <= 100


def test_copy_source_is_unchanged_and_recorded_as_old_path(git_repo):
    repo = git_repo()
    repo.commit({"src.txt": b"payload\n" * 20})
    copied = repo.commit_copy("src.txt", "dst.txt")
    changes = adapter().changes_for_commit(repo.path, copied, detect_renames=True, detect_copies=True)
    copy = [c for c in changes if c.change_type is GitChangeType.COPIED]
    assert copy and copy[0].old_path == "src.txt" and copy[0].new_path == "dst.txt"


def test_renames_disabled_yields_delete_and_add(git_repo):
    repo = git_repo()
    repo.commit({"old.txt": b"line one\nline two\nline three\n"})
    renamed = repo.commit_rename("old.txt", "new.txt")
    changes = adapter().changes_for_commit(repo.path, renamed, detect_renames=False, detect_copies=False)
    kinds = {c.change_type for c in changes}
    assert GitChangeType.RENAMED not in kinds
    assert {GitChangeType.DELETED, GitChangeType.ADDED} <= kinds
def test_enumerate_commits_rejects_revision_starting_with_dash(git_repo):
    repo = git_repo()
    with pytest.raises(GitRevisionError):
        adapter().enumerate_commits(repo.path, "--version")


def test_enumerate_commits_rejects_revision_containing_nul(git_repo):
    repo = git_repo()
    with pytest.raises(GitRevisionError):
        adapter().enumerate_commits(repo.path, "HEAD\x00")


def test_enumerate_commits_rejects_option_injection_before_running_git(git_repo, monkeypatch):
    repo = git_repo()
    calls = []
    original = adapter_module.GitAdapter.run_raw

    def spy(self, args, repository_path=None, **kw):
        calls.append(list(args))
        return original(self, args, repository_path, **kw)

    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    with pytest.raises(GitRevisionError):
        adapter().enumerate_commits(repo.path, "--upload-pack=evil")
    assert calls == []


def test_enumerate_commits_places_the_revision_after_end_of_options_and_paths_after_separator(
    git_repo, monkeypatch
):
    repo = git_repo()
    second = repo.commit({"a.txt": b"2\n"})
    calls = []
    original = adapter_module.GitAdapter.run_raw

    def spy(self, args, repository_path=None, **kw):
        calls.append(list(args))
        return original(self, args, repository_path, **kw)

    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    selected = adapter().enumerate_commits(
        repo.path, second, max_commits=None, include_merges=True, path_filter=("a.txt",)
    )
    assert selected.commit_ids == (second,)
    rev_list = [call for call in calls if call and call[0] == "rev-list"]
    assert len(rev_list) == 1
    args = rev_list[0]
    assert args.index("--end-of-options") < args.index(second)
    assert args.index("--") > args.index(second)
    assert ":(literal)a.txt" in args[args.index("--") + 1:]


def test_enumerate_commits_accepts_a_full_commit_id_and_revision_expression(git_repo):
    repo = git_repo()
    first = repo.commit({"a.txt": b"1\n"})
    second = repo.commit({"a.txt": b"2\n"})
    by_id = adapter().enumerate_commits(repo.path, second).commit_ids
    by_expression = adapter().enumerate_commits(repo.path, f"{second}^").commit_ids
    assert by_id == (first, second)
    assert by_expression == (first,)
