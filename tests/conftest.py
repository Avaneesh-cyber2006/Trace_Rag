"""Shared fixtures for Module 7 tests."""

from pathlib import PurePosixPath
from pathlib import Path
from types import SimpleNamespace
import os
import subprocess
import tempfile

import pytest

from backend.code_parser.models import (
    CodeParseInventory,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
    SourceLocation,
    SymbolInfo,
    SymbolKind,
)
from backend.file_scanner.models import FileCategory, FileInventory, ScannedFile


@pytest.fixture
def cg():
    def loc(start=0, end=100):
        return SourceLocation(start, end, 1, start, 1, end)

    def symbol(
        name,
        *,
        kind=SymbolKind.FUNCTION,
        qualified_name=None,
        parent=None,
        start=0,
        end=100,
        parameters=(),
        modifiers=(),
        base_types=(),
        implemented_types=(),
    ):
        return SymbolInfo(
            name,
            kind,
            qualified_name or name,
            parent,
            loc(start, end),
            parameters,
            None,
            modifiers,
            base_types,
            implemented_types,
        )

    def parsed(
        path="a.py",
        *,
        language=ParsedLanguage.PYTHON,
        status=ParseStatus.SUCCESS,
        symbols=(),
        imports=(),
        calls=(),
        issues=(),
    ):
        return ParsedFile(
            path,
            language,
            status,
            symbols,
            imports,
            calls,
            issues,
            "0" * 64,
        )

    def pair(*files, namespace="repo"):
        scanned = []
        for file in files:
            path = PurePosixPath(file.relative_path)
            locations = [
                item.location
                for item in (*file.symbols, *file.imports, *file.calls)
            ]
            size = max([4096, *(item.end_byte for item in locations)])
            language = (
                "typescript"
                if file.language is ParsedLanguage.TSX
                else file.language.value
            )
            scanned.append(
                ScannedFile(
                    file.relative_path,
                    path.name,
                    path.suffix,
                    language,
                    FileCategory.SOURCE,
                    size,
                )
            )
        fi = FileInventory(
            "/inert/repo",
            len(files),
            len(files),
            0,
            tuple(scanned),
            (),
            (),
            namespace,
        )
        pi = CodeParseInventory(
            "/inert/repo",
            len(files),
            sum(f.status is ParseStatus.SUCCESS for f in files),
            sum(f.status is ParseStatus.PARTIAL for f in files),
            sum(f.status is ParseStatus.FAILED for f in files),
            0,
            tuple(files),
            (),
            namespace,
        )
        return fi, pi

    return SimpleNamespace(loc=loc, symbol=symbol, parsed=parsed, pair=pair)


def _run_git(args, cwd=None, input_data=None, check=True):
    res = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.decode(errors='replace')}")
    return res


class ShallowClone:
    def __init__(self, path, head):
        self.path = Path(path)
        self.head = head

    def linked_worktree(self, name):
        wt = self.path.parent / name
        _run_git(["worktree", "add", str(wt), "HEAD"], cwd=self.path)
        return wt


class GitRepoBuilder:
    def __init__(self, tmp_path, index):
        self._index = index
        self.path = tmp_path / f"repo-{index}"
        self.path.mkdir(parents=True, exist_ok=True)
        _run_git(["init", "-b", "main"], cwd=self.path)
        _run_git(["config", "user.name", "Test User"], cwd=self.path)
        _run_git(["config", "user.email", "test@example.com"], cwd=self.path)
        self._counter = 0

    @property
    def head(self):
        res = _run_git(["rev-parse", "HEAD"], cwd=self.path, check=False)
        if res.returncode != 0:
            return ""
        return res.stdout.decode().strip()

    def _write_files(self, files):
        for rel, content in files.items():
            p = self.path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)

    def _commit_with_message_file(self, message):
        if len(message) > 32000:
            fd, tmp = tempfile.mkstemp()
            os.close(fd)
            Path(tmp).write_text(message, encoding="utf-8")
            res = _run_git(["commit", "-F", tmp], cwd=self.path)
            os.unlink(tmp)
            return res
        return _run_git(["commit", "-m", message], cwd=self.path)

    def commit(self, files, message="commit"):
        self._write_files(files)
        _run_git(["add", "-A"], cwd=self.path)
        self._commit_with_message_file(message)
        return self.head

    def commit_empty(self, message="empty commit"):
        _run_git(["commit", "--allow-empty", "-m", message], cwd=self.path)
        return self.head

    def commit_with_timestamp(self, files, timestamp, message="commit"):
        self._write_files(files)
        _run_git(["add", "-A"], cwd=self.path)
        env = dict(os.environ)
        env["GIT_AUTHOR_DATE"] = timestamp
        env["GIT_COMMITTER_DATE"] = timestamp
        if len(message) > 32000:
            fd, tmp = tempfile.mkstemp()
            os.close(fd)
            Path(tmp).write_text(message, encoding="utf-8")
            subprocess.run(["git", "commit", "-F", tmp], cwd=self.path, env=env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.unlink(tmp)
        else:
            subprocess.run(["git", "commit", "-m", message], cwd=self.path, env=env, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return self.head

    def head_timestamp(self):
        res = _run_git(["log", "-1", "--format=%ct %tz"], cwd=self.path, check=False)
        res = _run_git(["log", "-1", "--format=%ct %z"], cwd=self.path)
        return res.stdout.decode().strip()

    def branch(self, name, at):
        _run_git(["branch", name, at], cwd=self.path)

    def checkout(self, name):
        _run_git(["checkout", name], cwd=self.path)

    def merge(self, other):
        _run_git(["merge", "--no-edit", other], cwd=self.path)
        return self.head

    def detach(self, sha):
        _run_git(["checkout", "--detach", sha], cwd=self.path)

    def commit_rename(self, old, new):
        _run_git(["mv", old, new], cwd=self.path)
        _run_git(["commit", "-m", "rename"], cwd=self.path)
        return self.head

    def commit_copy(self, src, dst):
        import shutil
        shutil.copy(self.path / src, self.path / dst)
        _run_git(["add", dst], cwd=self.path)
        _run_git(["commit", "-m", "copy"], cwd=self.path)
        return self.head

    def delete(self, path):
        _run_git(["rm", path], cwd=self.path)
        _run_git(["commit", "-m", "delete"], cwd=self.path)
        return self.head

    def shallow_clone(self, depth=1):
        dest = self.path.parent / (self.path.name + "-shallow")
        if dest.exists():
            import shutil
            shutil.rmtree(dest)
        url = "file:///" + str(self.path).replace("\\", "/")
        _run_git(["clone", "--depth", str(depth), url, str(dest)])
        res = _run_git(["rev-parse", "HEAD"], cwd=dest)
        head = res.stdout.decode().strip()
        sc = ShallowClone(dest, head)
        return sc

    def linked_worktree(self, name):
        wt = self.path.parent / name
        _run_git(["worktree", "add", str(wt), "HEAD"], cwd=self.path)
        return wt

    def is_unborn(self):
        r1 = _run_git(["symbolic-ref", "--quiet", "HEAD"], cwd=self.path, check=False)
        r2 = _run_git(["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"], cwd=self.path, check=False)
        return r1.returncode == 0 and r2.returncode != 0

    def install_hook(self, name, body):
        res = _run_git(["rev-parse", "--git-dir"], cwd=self.path)
        gd = Path(res.stdout.decode().strip())
        if not gd.is_absolute():
            gd = self.path / gd
        hooks = gd / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")

    def add_replacement_for_head(self, message):
        res = _run_git(["rev-parse", "HEAD^{tree}"], cwd=self.path)
        tree = res.stdout.decode().strip()
        res = _run_git(["log", "-1", "--format=%P"], cwd=self.path)
        parents = res.stdout.decode().strip().split()
        args = ["commit-tree", tree]
        for p in parents:
            args += ["-p", p]
        r = subprocess.run(["git", *args], cwd=self.path, input=message.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        new_commit = r.stdout.decode().strip()
        _run_git(["replace", self.head, new_commit], cwd=self.path)

    def head_message(self):
        res = _run_git(["log", "-1", "--format=%B"], cwd=self.path)
        return res.stdout.decode().strip()


@pytest.fixture
def git_repo(tmp_path):
    counter = {"n": 0}

    def factory():
        counter["n"] += 1
        return GitRepoBuilder(tmp_path, counter["n"])

    return factory
