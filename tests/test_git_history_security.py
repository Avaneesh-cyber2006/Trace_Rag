"""Tests for Module 7 security."""

import hashlib
from pathlib import Path

import pytest

from backend.git_history import GitHistoryAnalyzer, GitRevisionError
from backend.git_history import adapter as adapter_module
from backend.git_history.adapter import build_git_environment

FORBIDDEN_SUBCOMMANDS = frozenset({
    "clone", "fetch", "pull", "push", "checkout", "reset", "rebase",
    "merge", "commit", "tag", "gc", "prune", "filter-branch", "replace",
})


def _forbidden_subcommand(args):
    return args[0] if args and args[0] in FORBIDDEN_SUBCOMMANDS else None


def _repo_state(path):
    root = Path(path)
    git_dir = root / ".git"
    entries = []
    if git_dir.exists():
        for p in sorted(git_dir.rglob("*")):
            if p.is_file():
                h = hashlib.sha256(p.read_bytes()).hexdigest()
                entries.append((str(p.relative_to(git_dir)), p.stat().st_size, h))
    # HEAD content hash
    head = root / ".git" / "HEAD"
    head_hash = hashlib.sha256(head.read_bytes()).hexdigest() if head.exists() else None
    return (tuple(entries), head_hash)


def test_no_forbidden_git_subcommand_is_ever_invoked(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path=None, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    GitHistoryAnalyzer().analyze(repo.path)
    assert seen
    for args in seen:
        assert _forbidden_subcommand(args) is None, args


def test_forbidden_subcommand_detector_actually_detects():
    assert _forbidden_subcommand(["clone", "https://example.invalid/x.git"]) == "clone"
    assert _forbidden_subcommand(["push", "origin", "main"]) == "push"
    assert _forbidden_subcommand(["cat-file", "--batch"]) is None


def test_analyzed_repository_is_not_mutated(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    before = _repo_state(repo.path)
    GitHistoryAnalyzer().analyze(repo.path)
    assert _repo_state(repo.path) == before


def test_hooks_path_is_overridden_to_an_empty_directory(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.install_hook("post-commit", 'echo ran')
    spawned = []
    original_popen = adapter_module.subprocess.Popen
    def spy(*args, **kwargs):
        argv = kwargs.get("args")
        if argv is None:
            argv = args[0] if args else []
        spawned.append(list(argv))
        return original_popen(*args, **kwargs)
    monkeypatch.setattr(adapter_module.subprocess, "Popen", spy)
    GitHistoryAnalyzer().analyze(repo.path)
    assert spawned
    for argv in spawned:
        assert any(a.startswith("core.hooksPath=") for a in argv), argv
        hooks = [a for a in argv if a.startswith("core.hooksPath=")]
        assert hooks and Path(hooks[0].split("=", 1)[1]).name == "hooks"


def test_only_allowed_git_subcommands_are_invoked(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path=None, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    GitHistoryAnalyzer().analyze(repo.path)
    allowed = {"--version", "rev-parse", "symbolic-ref", "rev-list",
               "cat-file", "diff-tree"}
    assert seen
    for args in seen:
        assert args[0] in allowed, args


def test_textconv_and_external_diff_are_disabled(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path=None, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    GitHistoryAnalyzer().analyze(repo.path)
    diff_calls = [a for a in seen if a and a[0] == "diff-tree"]
    assert diff_calls
    for args in diff_calls:
        assert "--no-ext-diff" in args and "--no-textconv" in args


def test_replacement_objects_are_ignored(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n", "b.txt": b"2\n"})
    original_message = repo.head_message()
    repo.add_replacement_for_head("REPLACEMENT message")
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert (inv.commits[0].message or "").strip() == original_message.strip()
    assert "REPLACEMENT message" not in (inv.commits[0].message or "")


def test_replacement_refs_are_neither_read_nor_modified(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    repo.add_replacement_for_head("REPLACEMENT message")
    before = _repo_state(repo.path)
    GitHistoryAnalyzer().analyze(repo.path)
    assert _repo_state(repo.path) == before


def test_error_messages_do_not_leak_environment(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    monkeypatch.setenv("MY_SECRET_TOKEN", "s3cr3t")
    with pytest.raises(GitRevisionError) as excinfo:
        GitHistoryAnalyzer().analyze(repo.path, revision="no-such-revision")
    assert "s3cr3t" not in str(excinfo.value)


def test_revision_option_injection_is_rejected(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    with pytest.raises(GitRevisionError):
        GitHistoryAnalyzer().analyze(repo.path, revision="--upload-pack=evil")


def test_revision_containing_nul_is_rejected(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    with pytest.raises(GitRevisionError):
        GitHistoryAnalyzer().analyze(repo.path, revision="HEAD\x00")


def test_lazy_fetch_is_disabled_and_no_filter_is_requested(git_repo, monkeypatch, tmp_path):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    seen = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path=None, **kw):
        seen.append(list(args))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    GitHistoryAnalyzer().analyze(repo.path)
    for args in seen:
        assert "--filter" not in args
        assert "--depth" not in args
    env = build_git_environment(Path(tmp_path))
    assert env["GIT_NO_LAZY_FETCH"] == "1"


def test_hostile_environment_variables_do_not_reach_the_child(git_repo, monkeypatch):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    monkeypatch.setenv("GIT_DIR", "C:/attacker")
    monkeypatch.setenv("GIT_WORK_TREE", "C:/attacker")
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", "C:/attacker")
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", "C:/attacker")
    monkeypatch.setenv("GIT_PROXY_COMMAND", "evil")
    monkeypatch.setenv("GIT_SSH", "evil")
    monkeypatch.setenv("GIT_ASKPASS", "evil")
    monkeypatch.setenv("GIT_TRACE", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker")
    seen_env = []
    original = adapter_module.GitAdapter.run_raw
    def spy(self, args, repository_path=None, **kw):
        seen_env.append(dict(self.environment))
        return original(self, args, repository_path, **kw)
    monkeypatch.setattr(adapter_module.GitAdapter, "run_raw", spy)
    inv = GitHistoryAnalyzer().analyze(repo.path)
    assert inv.commit_count == 1
    for env in seen_env:
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
                     "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PROXY_COMMAND",
                     "GIT_SSH", "GIT_ASKPASS", "GIT_TRACE", "HTTPS_PROXY"):
            assert name not in env