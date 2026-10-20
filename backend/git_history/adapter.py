"""Controlled Git subprocess adapter for Module 7."""

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .exceptions import (
    GitCommandError,
    GitCommandTimeoutError,
    GitConfigurationError,
    GitOutputTooLargeError,
    GitRepositoryError,
    GitUnavailableError,
)

MINIMUM_GIT_VERSION = (2, 29)
AGGREGATE_TIMEOUT_SECONDS = 300.0
CUMULATIVE_OUTPUT_BUDGET_BYTES = 256 * 1024 * 1024
PER_CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def build_git_environment(helper_dir: Path) -> dict[str, str]:
    helper_dir = Path(helper_dir)
    helper_dir.mkdir(parents=True, exist_ok=True)
    config_path = helper_dir / "gitconfig"
    config_path.touch(exist_ok=True)
    hooks_dir = helper_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    env: dict[str, str] = {}
    # PATH
    path_val = os.environ.get("PATH") or os.environ.get("Path")
    if path_val is not None:
        env["PATH"] = path_val
    # platform essentials
    for key in ("SystemRoot", "Path", "TEMP", "TMP"):
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_ATTR_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_PAGER"] = "cat"
    env["PAGER"] = "cat"
    env["GIT_EXTERNAL_DIFF"] = ""
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_CONFIG_GLOBAL"] = str(config_path)
    return env


def terminate_process(process: subprocess.Popen) -> None:
    try:
        process.terminate()
    except Exception:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait()
        except Exception:
            pass
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
        try:
            process.wait()
        except Exception:
            pass


class GitAdapter:
    def __init__(self, *, git_executable: str = "git", command_timeout_seconds: float = 30.0,
                 max_output_bytes: int = 50 * 1024 * 1024, max_commits=None):
        if not isinstance(git_executable, str) or not git_executable:
            raise GitConfigurationError("git_executable must be nonempty")
        if not isinstance(command_timeout_seconds, (int, float)) or isinstance(command_timeout_seconds, bool) or command_timeout_seconds <= 0:
            raise GitConfigurationError("command_timeout_seconds must be > 0")
        if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes <= 0:
            raise GitConfigurationError("max_output_bytes must be > 0")
        if max_commits is not None:
            if not isinstance(max_commits, int) or isinstance(max_commits, bool) or max_commits <= 0:
                raise GitConfigurationError("max_commits must be positive")
        self.git_executable = git_executable
        self.command_timeout_seconds = float(command_timeout_seconds)
        self.max_output_bytes = int(max_output_bytes)
        self.max_commits = max_commits
        self._helper_dir = Path(tempfile.mkdtemp(prefix="trace_rag_git_"))
        self._hooks_dir = self._helper_dir / "hooks"
        self._hooks_dir.mkdir(exist_ok=True)
        config_path = self._helper_dir / "gitconfig"
        config_path.touch(exist_ok=True)
        self._environment = build_git_environment(self._helper_dir)
        self._cumulative_bytes = 0
        self._cumulative_lock = threading.Lock()
        self._started_at = time.monotonic()

    def git_version(self) -> str:
        res = self.run_raw(["--version"])
        out = res.stdout.decode("utf-8", errors="replace").strip()
        # parse
        # e.g. git version 2.55.0.windows.5
        version_str = out
        # extract numbers
        import re
        m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", version_str)
        if m:
            major = int(m.group(1))
            minor = int(m.group(2))
            if (major, minor) < MINIMUM_GIT_VERSION:
                raise GitUnavailableError("git version too old")
        # lazy floor check using module constant
        if (major, minor) < MINIMUM_GIT_VERSION:
            raise GitUnavailableError("git version too old")
        return version_str

    def inspect_repository(self, repository_path):
        rp = Path(repository_path)
        if not rp.exists() or not rp.is_dir():
            raise GitRepositoryError("not a repository")
        r1 = self.run_raw(["rev-parse", "--is-inside-work-tree"], rp)
        if r1.returncode != 0:
            raise GitRepositoryError("not a repository")
        inside = r1.stdout.decode().strip() == "true"
        r2 = self.run_raw(["rev-parse", "--is-bare-repository"], rp)
        bare = r2.stdout.decode().strip() == "true" if r2.returncode == 0 else False
        r3 = self.run_raw(["rev-parse", "--is-shallow-repository"], rp)
        shallow = r3.stdout.decode().strip() == "true" if r3.returncode == 0 else False
        r4 = self.run_raw(["rev-parse", "--git-common-dir"], rp)
        common_dir_raw = r4.stdout.decode().strip() if r4.returncode == 0 else ".git"
        common_dir = Path(common_dir_raw)
        if not common_dir.is_absolute():
            common_dir = (rp / common_dir).resolve()
        else:
            common_dir = common_dir.resolve()
        gv = self.git_version()
        return GitRepositoryContext(
            is_bare=bare,
            is_shallow=shallow,
            git_common_dir=common_dir,
            git_version=gv,
            is_inside_work_tree=inside,
        )

    def resolve_revision(self, repository_path, revision):
        rp = Path(repository_path)
        if not isinstance(revision, str) or revision.startswith("-") or "\x00" in revision:
            raise GitRevisionError("invalid revision")
        res = self.run_raw(["rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"], rp)
        if res.returncode != 0:
            raise GitRevisionError("invalid revision")
        return res.stdout.decode().strip()

    def is_detached_head(self, repository_path):
        rp = Path(repository_path)
        res = self.run_raw(["symbolic-ref", "--quiet", "HEAD"], rp)
        return res.returncode != 0

    def is_unborn_head(self, repository_path):
        rp = Path(repository_path)
        r1 = self.run_raw(["symbolic-ref", "--quiet", "HEAD"], rp)
        r2 = self.run_raw(["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"], rp)
        return r1.returncode == 0 and r2.returncode != 0

    def enumerate_commits(self, repository_path, revision, *, max_commits=None, include_merges=True, path_filter=()):
        rp = Path(repository_path)
        # resolve effective max
        hard_cap = 100000
        if max_commits is None:
            effective = hard_cap
        else:
            effective = min(max_commits, hard_cap)
        k = effective + 1
        args = ["rev-list", "--topo-order", "--reverse", f"--max-count={k}"]
        if include_merges is False:
            args.append("--no-merges")
        args.append(revision)
        # path filter
        if path_filter:
            args.append("--")
            for p in path_filter:
                args.append(f":(literal){p}")
        res = self.run_raw(args, rp)
        if res.returncode != 0:
            raise GitCommandError(res.stderr.decode(errors="replace"))
        ids = [line.strip() for line in res.stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
        truncated = len(ids) > effective
        if truncated:
            ids = ids[:effective]
        return CommitSelection(tuple(ids), truncated)

    def fetch_commit_objects(self, repository_path, commit_ids):
        rp = Path(repository_path)
        payload = "".join(f"{cid}\n" for cid in commit_ids).encode("utf-8")
        res = self.run_raw(["cat-file", "--batch"], rp, stdin_data=payload)
        from .parser import parse_batch_stream
        parsed = parse_batch_stream(res.stdout, list(commit_ids))
        return parsed

    def resolve_baseline(self, repository_path, commit_id, raw, is_shallow=False):
        # returns BaselineResolution
        rp = Path(repository_path)
        # root?
        if len(raw.parent_ids) == 0:
            # empty tree based on id length
            if len(commit_id) == 64:
                et = "6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321"
            else:
                et = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
            return BaselineResolution(et, False)
        # non-root: resolve first parent tree
        try:
            res = self.run_raw(["rev-parse", "--verify", "--end-of-options", f"{commit_id}^1^{{tree}}"], rp)
        except Exception:
            return BaselineResolution(None, True)
        if res.returncode != 0:
            return BaselineResolution(None, True)
        tree = res.stdout.decode().strip()
        return BaselineResolution(tree, False)

    def diff_trees(self, repository_path, baseline_tree, commit_tree, detect_renames=True, detect_copies=False):
        rp = Path(repository_path)
        args = ["diff-tree", "--no-commit-id", "-r", "-z", "--no-ext-diff", "--no-textconv", "--no-color", "--name-status"]
        if detect_renames:
            args.append("--find-renames")
        if detect_copies:
            args.append("--find-copies-harder")
        if detect_copies and not detect_renames:
            args.append("--no-renames")
        args.append(baseline_tree)
        args.append(commit_tree)
        res = self.run_raw(args, rp)
        from .parser import parse_name_status
        return parse_name_status(res.stdout)

    def changes_for_commit(self, repository_path, commit_id, raw, *, detect_renames=True, detect_copies=False, is_shallow=False):
        br = self.resolve_baseline(repository_path, commit_id, raw, is_shallow)
        if br.unavailable or br.baseline_tree is None:
            return ()
        return self.diff_trees(repository_path, br.baseline_tree, raw.tree_id,
                              detect_renames=detect_renames, detect_copies=detect_copies)

    def close(self):
        import shutil
        try:
            shutil.rmtree(self._helper_dir, ignore_errors=True)
        except Exception:
            pass

    def __del__(self):
        self.close()

    @property
    def environment(self) -> dict:
        return dict(self._environment)

    @property
    def cumulative_output_bytes(self) -> int:
        with self._cumulative_lock:
            return self._cumulative_bytes

    def consume_output_budget(self, amount: int) -> None:
        with self._cumulative_lock:
            self._cumulative_bytes += amount
            if self._cumulative_bytes > CUMULATIVE_OUTPUT_BUDGET_BYTES:
                raise GitOutputTooLargeError("cumulative output budget exceeded")

    def check_aggregate_deadline(self) -> None:
        agg = AGGREGATE_TIMEOUT_SECONDS
        if time.monotonic() > self._started_at + agg:
            raise GitCommandTimeoutError("aggregate deadline exceeded")

    def read_shallow_boundary_ids(self, git_common_dir) -> tuple[str, ...]:
        d = Path(git_common_dir)
        shallow_file = d / "shallow"
        if not d.is_absolute():
            # resolve by caller? assume as given
            pass
        if not shallow_file.exists():
            return ()
        ids = []
        for line in shallow_file.read_text().splitlines():
            line = line.strip()
            if line:
                ids.append(line)
        return tuple(ids)

    def run_raw(self, args, repository_path=None, *, stdin=None, stdin_data=None):
        # resolve stdin_data
        if stdin_data is None and stdin is not None:
            stdin_data = stdin
        self.check_aggregate_deadline()
        # build command
        hooks_dir = str(self._hooks_dir)
        if repository_path is None:
            cmd = [self.git_executable, "-c", f"core.hooksPath={hooks_dir}", *list(args)]
        else:
            rp = Path(repository_path)
            if not rp.exists() or not rp.is_dir():
                raise GitRepositoryError("not a directory")
            cmd = [self.git_executable, "-C", str(rp), "-c", f"core.hooksPath={hooks_dir}", *list(args)]
        # effective timeout
        agg = AGGREGATE_TIMEOUT_SECONDS
        remaining = (self._started_at + agg) - time.monotonic()
        if remaining <= 0:
            self.check_aggregate_deadline()
        effective_timeout = min(self.command_timeout_seconds, max(0.0, remaining)) if remaining > 0 else 0.0
        if effective_timeout <= 0:
            raise GitCommandTimeoutError("aggregate deadline exceeded")

        # spawn
        use_stdin_pipe = stdin_data is not None
        if use_stdin_pipe:
            stdin_handle = subprocess.PIPE
        else:
            stdin_handle = subprocess.DEVNULL
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=stdin_handle,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                env=self._environment,
            )
        except OSError as e:
            raise GitUnavailableError(str(e))

        stdout_buf = bytearray()
        stderr_buf = bytearray()
        stdout_lock = threading.Lock()
        stderr_lock = threading.Lock()
        combined_lock = self._cumulative_lock
        error_state = {"exc": None}

        def reader(pipe, buf, lock):
            try:
                while True:
                    chunk = pipe.read(PER_CHUNK)
                    if not chunk:
                        break
                    with combined_lock:
                        self._cumulative_bytes += len(chunk)
                        if self._cumulative_bytes > CUMULATIVE_OUTPUT_BUDGET_BYTES:
                            error_state["exc"] = GitOutputTooLargeError("cumulative output budget exceeded")
                    with lock:
                        buf.extend(chunk)
                        current = len(buf)
                    # per-command combined cap
                    with self._cumulative_lock:
                        pass
                    # check per-command combined
                    with stdout_lock:
                        so = len(stdout_buf)
                    with stderr_lock:
                        se = len(stderr_buf)
                    if so + se > self.max_output_bytes:
                        error_state["exc"] = GitOutputTooLargeError("output too large")
                    if error_state["exc"] is not None:
                        terminate_process(proc)
                        return
            except Exception as e:
                error_state["exc"] = GitCommandError(str(e))
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        def writer():
            if not use_stdin_pipe:
                return
            try:
                # write in chunks
                data = stdin_data
                offset = 0
                chunk_size = 65536
                while offset < len(data):
                    try:
                        proc.stdin.write(data[offset:offset + chunk_size])
                        proc.stdin.flush()
                    except BrokenPipeError:
                        return
                    offset += chunk_size
            except Exception:
                pass
            finally:
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                except Exception:
                    pass

        t_out = threading.Thread(target=reader, args=(proc.stdout, stdout_buf, stdout_lock), daemon=True)
        t_err = threading.Thread(target=reader, args=(proc.stderr, stderr_buf, stderr_lock), daemon=True)
        t_in = threading.Thread(target=writer, daemon=True)
        t_out.start()
        t_err.start()
        if use_stdin_pipe:
            t_in.start()

        timed_out = False
        try:
            proc.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_process(proc)
            error_state["exc"] = GitCommandTimeoutError("command timed out")

        # join threads
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        if use_stdin_pipe:
            t_in.join(timeout=2)

        # determine exception
        exc = error_state["exc"]
        if timed_out:
            exc = GitCommandTimeoutError("command timed out")
        # per-command cap check final combined
        if exc is None:
            with stdout_lock:
                so = len(stdout_buf)
            with stderr_lock:
                se = len(stderr_buf)
            if so + se > self.max_output_bytes:
                exc = GitOutputTooLargeError("output too large")
        if exc is not None:
            raise exc

        return CommandResult(proc.returncode, bytes(stdout_buf), bytes(stderr_buf))

    def run(self, args, repository_path, *, stdin=None, stdin_data=None):
        if stdin_data is None and stdin is not None:
            stdin_data = stdin
        result = self.run_raw(args, repository_path, stdin_data=stdin_data)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")
            # sanitize: truncate
            excerpt = stderr[:500]
            raise GitCommandError(f"git command failed: {excerpt}")
        return result.stdout
from dataclasses import dataclass as _dc


@_dc(frozen=True, slots=True)
class GitRepositoryContext:
    is_bare: bool
    is_shallow: bool
    git_common_dir: "Path"
    git_version: str
    is_inside_work_tree: bool = True


@_dc(frozen=True, slots=True)
class CommitSelection:
    commit_ids: tuple[str, ...]
    truncated: bool


@_dc(frozen=True, slots=True)
class BaselineResolution:
    baseline_tree: "str | None"
    unavailable: bool
def build_diff_flags(detect_renames: bool, detect_copies: bool) -> list[str]:
    flags = []
    if detect_renames:
        flags.append("--find-renames")
    if detect_copies:
        flags.append("--find-copies-harder")
    if detect_copies and not detect_renames:
        flags.append("--no-renames")
    return flags
