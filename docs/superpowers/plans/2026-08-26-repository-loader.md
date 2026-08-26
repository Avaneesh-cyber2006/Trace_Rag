# TraceRAG Repository Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe, production-quality loader for public GitHub repository roots that returns basic clone metadata as `RepositoryInfo`.

**Architecture:** Keep URL parsing, data models, errors, filesystem/Git metadata, and clone/workspace orchestration in focused modules. Use Git CLI only for availability and shallow cloning, GitPython for read-only repository metadata, and dataclasses for dependency-light structured values.

**Tech Stack:** Python 3.11+, pathlib, urllib.parse, subprocess, GitPython, pytest

**Spec:** `docs/superpowers/specs/2026-08-26-repository-loader-design.md`

## Global Constraints

- Support only public `github.com` repository root URLs over HTTP or HTTPS.
- Use `git clone --depth 1`; never use `shell=True` or execute repository-controlled code.
- Never overwrite, update, or delete a path that existed before a load attempt.
- Exclude `.git` from working-tree file count and byte size.
- Translate implementation errors into the Repository Loader exception hierarchy.
- Remain portable across Windows 10/11 and Linux with Python 3.11+.
- Do not implement any Module 2+ behavior.

---

### Task 1: URL Contract, Models, and Exceptions

**Files:**
- Create: `backend/__init__.py`
- Create: `backend/repository_loader/__init__.py`
- Create: `backend/repository_loader/exceptions.py`
- Create: `backend/repository_loader/models.py`
- Create: `backend/repository_loader/validator.py`
- Create: `tests/test_repository_loader.py`

**Interfaces:**
- Produces: `GitHubRepositoryURL`, `RepositoryInfo`, `validate_github_repository_url(repository_url: str) -> GitHubRepositoryURL`, and all specified exceptions.

- [ ] **Step 1: Write failing parameterized URL tests**

  Cover the three accepted forms and literal expected owner, name, and HTTPS normalized URL. Cover missing/extra path segments, wrong host/scheme, credentials, ports, queries/fragments, encoded separators, dot segments, and unsafe owner/repository characters; every rejection must raise `InvalidRepositoryURL`.

- [ ] **Step 2: Run URL tests and verify RED**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: collection/import failure because the package does not exist.

- [ ] **Step 3: Implement the minimal contracts and validator**

  Add an immutable `GitHubRepositoryURL(owner, repository_name, normalized_url)` dataclass and immutable `RepositoryInfo(success, owner, repository_name, repo_url, local_path, branch, current_commit, total_files, repository_size_bytes, reused_existing_clone)`. Add the complete exception hierarchy. Parse with `urlsplit`, require exact root shape, strip one `.git` suffix, validate GitHub-safe ASCII path components, and construct the normalized URL from validated components.

- [ ] **Step 4: Run URL tests and verify GREEN**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: all URL tests pass.

### Task 2: Working-Tree Metadata

**Files:**
- Create: `backend/repository_loader/metadata.py`
- Modify: `tests/test_repository_loader.py`

**Interfaces:**
- Consumes: validated owner/name/URL and an existing repository path.
- Produces: `extract_repository_info(repo_path: Path, repository_url: GitHubRepositoryURL, reused_existing_clone: bool) -> RepositoryInfo` and `get_normalized_origin_url(repo_path: Path) -> GitHubRepositoryURL`.

- [ ] **Step 1: Write failing metadata tests using a real temporary Git repository**

  Initialize a repository, configure local author identity, commit known files, add a GitHub origin, and assert literal branch/SHA, file count, byte sum, resolved path, and reuse flag. Add a `.git` payload large enough to prove it is excluded and detach HEAD to assert `branch is None`.

- [ ] **Step 2: Run metadata tests and verify RED**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: import failure for `metadata` or missing functions.

- [ ] **Step 3: Implement read-only metadata extraction**

  Open with `git.Repo`, translate invalid/corrupt repository failures to `MetadataExtractionError`, read `active_branch` only when not detached, read `head.commit.hexsha`, and walk with `os.scandir` while pruning `.git`. Skip transient `OSError` entries and never open file contents.

- [ ] **Step 4: Run metadata tests and verify GREEN**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: all validator and metadata tests pass.

### Task 3: Safe Workspace, Clone, Reuse, and Error Translation

**Files:**
- Create: `backend/repository_loader/loader.py`
- Modify: `backend/repository_loader/__init__.py`
- Modify: `tests/test_repository_loader.py`

**Interfaces:**
- Consumes: validator and metadata interfaces.
- Produces: `RepositoryLoader.__init__(workspace_path: Path | str = "workspace", clone_timeout_seconds: float = 120)`; `RepositoryLoader.load(repository_url: str) -> RepositoryInfo`; and `load_repository(...) -> RepositoryInfo`.

- [ ] **Step 1: Write failing loader behavior tests**

  Use a real local temporary source repository with an injected clone runner or precise subprocess boundary to verify destination naming, shallow-clone arguments, successful metadata, and reuse of a matching existing clone. Assert that non-repositories and mismatched origins raise `WorkspaceError` without changing their sentinel files.

- [ ] **Step 2: Write failing process failure and cleanup tests**

  Patch only `subprocess.run` to induce missing Git, timeout, known not-found/private diagnostics, and generic clone failure. Assert the exact module exception type and that only a newly created partial destination is removed while pre-existing paths remain untouched.

- [ ] **Step 3: Run loader tests and verify RED**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: missing `RepositoryLoader` behavior.

- [ ] **Step 4: Implement safe loading**

  Resolve/create workspace, derive and containment-check a single deterministic child destination, check Git with `git --version`, invoke `git -c core.hooksPath=<empty-safe-path> clone --depth 1 <url> <destination>` with `GIT_TERMINAL_PROMPT=0`, timeout, captured output, and no shell. Reuse only matching origins. Classify safe stderr fragments, log lifecycle events, and clean only a destination absent at entry.

- [ ] **Step 5: Run loader tests and verify GREEN**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: all unit tests pass.

### Task 4: User-Facing Script, Packaging, and Documentation

**Files:**
- Create: `scripts/test_repository_loader.py`
- Create: `workspace/.gitkeep`
- Create: `requirements.txt`
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `README.md`
- Modify: `tests/test_repository_loader.py`

**Interfaces:**
- Produces: documented installation/usage, offline-default pytest configuration, optional `integration` test, and a CLI returning zero on success/nonzero on `RepositoryLoaderError`.

- [ ] **Step 1: Write failing CLI behavior tests**

  Invoke the script entry function with an injected loader for success and failure. Assert human-readable status, repository metadata, friendly byte formatting, and exit codes without testing exact whitespace.

- [ ] **Step 2: Run CLI tests and verify RED**

  Run: `python -m pytest tests/test_repository_loader.py -q`
  Expected: missing script module or entry function.

- [ ] **Step 3: Implement CLI and repository support files**

  Use argparse, catch only `RepositoryLoaderError` at the normal caller boundary, and format metadata without tracebacks. Add minimal GitPython/pytest dependencies, the integration marker excluded by default, workspace and secret ignores, and README sections required by the specification.

- [ ] **Step 4: Add the optional integration test**

  Mark a clone of a known small public repository with `@pytest.mark.integration`; verify normalized URL, commit, files, destination, and shallow status. Keep it excluded from ordinary `pytest -q`.

- [ ] **Step 5: Run the complete offline suite**

  Run: `python -m pytest -q`
  Expected: all unit tests pass with integration deselected and no warnings.

### Task 5: Final Verification

**Files:**
- Modify only files needed to correct verification failures, always after adding a reproducing failing test.

**Interfaces:**
- Produces: verified Module 1 repository state.

- [ ] **Step 1: Run static repository checks**

  Run: `git diff --check` and `python -m compileall -q backend scripts tests`
  Expected: both exit zero.

- [ ] **Step 2: Run the full unit suite fresh**

  Run: `python -m pytest -q`
  Expected: every offline test passes.

- [ ] **Step 3: Run network verification when available**

  Run: `python -m pytest -q -m integration` and `python scripts/test_repository_loader.py https://github.com/octocat/Hello-World`.
  Expected when network is available: both succeed and create `workspace/octocat_Hello-World`.

- [ ] **Step 4: Verify the resulting clone**

  Run: `git -C workspace/octocat_Hello-World rev-parse HEAD` and `git -C workspace/octocat_Hello-World rev-parse --is-shallow-repository`.
  Expected: a 40-character SHA and `true`.

- [ ] **Step 5: Confirm scope and status**

  Inspect `git status --short` and repository files. Confirm no downloaded repository is tracked, no Module 2+ dependency/behavior exists, and record exact verification outcomes for the final response.
