# Module 7 — Git History Analyzer

Date: 2026-10-18

Status: design specification for review. This document defines behavior and
boundaries, not an implementation plan. No production code or tests are created
by this document.

## 1. Purpose and dependency position

Module 7 converts a local Git repository's commit history into a deterministic,
immutable, in-memory `GitHistoryInventory` of historical facts.

It is the seventh TraceRAG module. Its role is to provide structured historical
evidence: commits, parent relationships, file changes, rename/copy detection
evidence, author/committer metadata, commit messages, and bounded traversal
indexes. It does **not** interpret history causally. A commit that changed two
files together is evidence of co-change, not proof of logical dependency. A
rename detected by Git is evidence of path lineage, not proof of semantic
continuity.

```text
Module 1 -> Module 2 -> Module 3
                            /       \
                           v         v
                       Module 4   Module 6 <- Module 2
                        Chunks    Code Graph
                           |         |
                           v         |
                       Module 5      |
                    Semantic Index   |
                            \       /
                             Module 9
                            Hybrid RAG
                               ^
                               |
                          Module 7
                       Git History
                               ^
                               |
                          Module 1
                    Repository Loader
```

Module 7 depends on Module 1 (repository location and repository identity) and
optionally on Module 2 (file inventory, for namespace and path validation). It
does **not** depend on Module 3, 4, 5, or 6. It may be consumed later by
Modules 8-10 and by Module 9 as historical evidence. Mentioning downstream
consumers here does not authorize Modules 8-10 work.

## 2. Existing contracts and compatibility

The authoritative public definitions are:

- `backend/repository_loader/models.py` (`RepositoryInfo`, `GitHubRepositoryURL`)
- `backend/repository_loader/__init__.py`
- `backend/file_scanner/models.py` (`FileInventory`, `ScannedFile`)
- `backend/file_scanner/__init__.py`
- `backend/code_parser/models.py` (`ParsedLanguage`, `ParseStatus`)
- `backend/code_graph/models.py` (`CodeGraphInventory`, `GraphNode`, `GraphNodeKind`)
- `backend/code_graph/__init__.py`

Key facts established by the existing contracts:

- `RepositoryInfo.local_path` is a string path to a cloned or reused repository.
  `RepositoryInfo.current_commit` is the checked-out commit at load time.
  `RepositoryInfo.branch` may be `None` (detached HEAD).
- `RepositoryInfo.repository_name` and `owner` identify the repository.
  `repository_namespace` is a Module 2 concept (`str | None`); Module 7
  requires a nonempty string namespace when association with Module 2 or
  Module 6 is requested, and rejects `None` in that case. For standalone
  historical analysis, the namespace may be `None`.
- `FileInventory.repository_path` is an absolute resolved path string.
  `FileInventory.repository_namespace` is `str | None`.
  `ScannedFile.relative_path` uses POSIX separators.
- `CodeGraphInventory.repository_namespace` is a nonempty string.
  `GraphNode.relative_path` uses POSIX separators.
  `GraphNode.node_kind` is `FILE` or `SYMBOL`.
- Existing modules use frozen, slotted dataclasses, tuple collections,
  `MappingProxyType` for published indexes, and typed exception hierarchies.
- Module 6 identity namespaces (`code-graph:*`) are not reused by Module 7.
- Module 1 already performs controlled Git subprocess calls with timeouts,
  isolated configuration, a fresh hooks directory, and no network. Module 7
  follows the same safety posture but generalizes it for read-only history
  extraction.

Module 7 must not modify any of these upstream contracts.

## 3. Public API and ownership

```python
from pathlib import Path
from typing import Sequence

class GitHistoryAnalyzer:
    def __init__(
        self,
        *,
        git_executable: str = "git",
        command_timeout_seconds: float = 30.0,
        max_output_bytes: int = 50 * 1024 * 1024,
        max_commits: int | None = None,
    ) -> None: ...

    def analyze(
        self,
        repository_path: str | Path,
        *,
        repository_namespace: str | None = None,
        file_inventory: FileInventory | None = None,
        revision: str = "HEAD",
        max_commits: int | None = None,
        include_merges: bool = True,
        detect_renames: bool = True,
        detect_copies: bool = False,
        path_filter: Sequence[str] = (),
        include_commit_messages: bool = True,
    ) -> GitHistoryInventory: ...

def analyze_git_history(
    repository_path: str | Path,
    *,
    repository_namespace: str | None = None,
    file_inventory: FileInventory | None = None,
    revision: str = "HEAD",
    max_commits: int | None = None,
    include_merges: bool = True,
    detect_renames: bool = True,
    detect_copies: bool = False,
    path_filter: Sequence[str] = (),
    include_commit_messages: bool = True,
    git_executable: str = "git",
    command_timeout_seconds: float = 30.0,
    max_output_bytes: int = 50 * 1024 * 1024,
) -> GitHistoryInventory: ...
```

Rules:

- Constructor arguments are validated: `command_timeout_seconds` must be > 0,
  `max_output_bytes` must be > 0, `git_executable` must be a nonempty string;
  invalid values raise `GitConfigurationError`. `max_commits`, when not `None`,
  must be a positive integer. The internal 100,000-commit hard cap also uses the
  N+1 lookahead (request 100,001; truncate if exceeded), so a repository with
  exactly 100,000 commits is not falsely marked truncated.
- `repository_path` must resolve to an existing directory containing a valid
  Git repository (worktree or bare). If it does not, raise `GitRepositoryError`
  (or a subclass).
- `revision` is a Git revision expression. It must not begin with `-` or contain
  NUL. It is validated through
  `git rev-parse --verify --end-of-options <revision>^{commit}`. Invalid
  revisions raise `GitRevisionError`. A valid repository with no commits (unborn
  HEAD) is not an error when `revision` is an unborn symbolic ref (detected as
  `git symbolic-ref --quiet <revision>` succeeding while
  `git rev-parse --verify --end-of-options <revision>^{commit}` fails): the
  analyzer returns an empty inventory (`commits=()`, `changes=()`) with
  `history_is_complete=True` (subject to shallow detection, which is false for
  an empty repository). If the repository has commits and `revision` does not
  resolve, `GitRevisionError` is raised.
- `max_commits` overrides the constructor default for this call. `None` means
  use the constructor's `max_commits`; if both are `None`, analyze all reachable
  commits, subject to the internal hard cap of 100000 commits. If the cap is
  reached, `truncated_by_limit` is set and analysis stops. A constructor
  `max_commits` of `None` with an analyze `max_commits` of `None` is the only
  way to request "all".
- `path_filter` is a sequence of repository-relative POSIX paths. Each is
  validated: nonempty, no NUL, no backslash, forward slashes only, no leading
  slash, no `.` or `..` component, no empty component. It restricts COMMIT
  SELECTION to commits touching at least one listed path. Every selected
  commit retains its full first-parent file-change set (changes are not
  filtered). Duplicate entries are deduplicated and sorted for scope storage.
  `PATH_FILTER_APPLIED` is recorded in `incomplete_reasons`.
- `file_inventory`, when provided, supplies `repository_namespace` (if
  `repository_namespace` is `None`) and may validate that its `repository_path`
  resolves to the same root as `repository_path`. Changed paths are not required
  to appear in the inventory (deleted files will not). If both namespace and
  `repository_path` are provided, they must agree with the inventory or
  `GitInventoryMismatchError` is raised. A non-`None` namespace must be a
  nonempty string; an empty string raises `GitConfigurationError`.
- `include_commit_messages=False` replaces every message with `None` and is
  intended for privacy-sensitive contexts. The commit still exists.
- The API has no filesystem mutation, no network, and no source-reading
  callback. It reads Git metadata only.

Public exports: `GitHistoryAnalyzer`, `analyze_git_history`, all model classes,
all enums, and the typed exception hierarchy.

## 4. Data models and invariants

All public models are frozen, slotted dataclasses. Collections are tuples.
Published indexes are `MappingProxyType` snapshots created in `__post_init__`
from copied backing stores. Models validate their invariants in
`__post_init__` and raise the appropriate typed exception on violation.

### 4.1 Enums

```python
class GitChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"
    COPIED = "copied"
    TYPE_CHANGED = "type_changed"
    UNMERGED = "unmerged"
    UNKNOWN = "unknown"

class GitIncompleteReason(str, Enum):
    SHALLOW_HISTORY = "shallow_history"
    MAX_COMMITS_REACHED = "max_commits_reached"
    PARENT_UNAVAILABLE = "parent_unavailable"
    MERGE_SEMANTICS_PARTIAL = "merge_semantics_partial"
    MERGES_EXCLUDED = "merges_excluded"
    MISSING_METADATA = "missing_metadata"
    PATH_FILTER_APPLIED = "path_filter_applied"
    CO_CHANGE_LIMIT_EXCEEDED = "co_change_limit_exceeded"

class GitErrorCode(str, Enum):
    NOT_A_REPOSITORY = "not_a_repository"
    GIT_UNAVAILABLE = "git_unavailable"
    INVALID_REVISION = "invalid_revision"
    COMMAND_FAILED = "command_failed"
    COMMAND_TIMEOUT = "command_timeout"
    OUTPUT_TOO_LARGE = "output_too_large"
    PARSE_ERROR = "parse_error"
    INVENTORY_MISMATCH = "inventory_mismatch"
```

`GitErrorCode` is reserved for future association with the exception
hierarchy; v1 classifies errors by exception class (see 4.6).

### 4.2 GitCommit

```python
@dataclass(frozen=True, slots=True)
class GitCommit:
    commit_id: str                      # full 40-hex SHA-1 (or SHA-256 for new repos)
    parent_ids: tuple[str, ...]
    author_name: str
    author_email: str
    author_timestamp: int               # Unix epoch seconds
    author_timezone: str               # e.g. "+02:00"
    committer_name: str
    committer_email: str
    committer_timestamp: int
    committer_timezone: str
    message: str | None                # None when include_commit_messages=False
    is_merge: bool
    is_root: bool
```

Invariants:
- `commit_id` matches `^(?:[0-9a-f]{40}|[0-9a-f]{64})$`.
- `parent_ids` contains full object IDs in Git's original parent order (first
  parent first). No duplicates. Order is NOT sorted; Git parent ordering is
  significant (first parent defines the mainline). A shallow boundary commit
  whose raw object has no `parent` lines is genuinely root; one whose raw
  object has `parent` lines is not root and records `PARENT_UNAVAILABLE`.
- `is_merge == (len(parent_ids) > 1)`.
- `is_root == (len(parent_ids) == 0)`.
- Timestamps are integers (Git may record pre-1970 commits with negative
  epoch values; these are accepted and preserved).
- `author_timezone`/`committer_timezone` match `^[+-]\d{2}:\d{2}$`. Raw Git
  timezone offsets (Git writes `±HHMM`) are normalized to `±HH:MM`. A
  missing author/committer header records `MISSING_METADATA` with
  `author_timezone`/`committer_timezone` set to `+00:00` and timestamps
  set to 0.
- `message` is `None` only when messages were suppressed; otherwise a string
  (possibly empty).

### 4.3 GitFileChange

```python
@dataclass(frozen=True, slots=True)
class GitFileChange:
    change_id: str
    commit_id: str
    change_type: GitChangeType
    old_path: str | None
    new_path: str | None
    similarity: int | None            # 0..100 for rename/copy, else None
```

Invariants:
- `change_id` is a lowercase SHA-256 hex digest. Its input is the UTF-8 bytes of
  `git-history:change:v1:` followed by the canonical JSON serialization of the
  object `{"commit_id":..., "change_type":..., "old_path":..., "new_path":...}`
  with `ensure_ascii=False`, `separators=(",", ":")`, and `sort_keys=True`.
  `change_type` serializes as its string value; `old_path`/`new_path` serialize as
  strings or JSON `null`. No other fields enter the digest.
- At least one of `old_path`/`new_path` is non-`None`.
- `similarity` is `None` unless `change_type` is `RENAMED` or `COPIED`.
- `old_path`/`new_path` are POSIX paths relative to repository root.
- For `ADDED`, only `new_path` is set. For `DELETED`, only `old_path`.
  For `RENAMED`/`COPIED`, both are set. For `MODIFIED`, `TYPE_CHANGED`,
  `UNMERGED`, and `UNKNOWN`, only `new_path` is set.
- Diff statistics (additions/deletions/binary status) are out of scope for v1:
  extraction is name-status only, so no such fields exist.

### 4.4 GitHistoryScope

```python
@dataclass(frozen=True, slots=True)
class GitHistoryScope:
    revision: str
    path_filter: tuple[str, ...]
    max_commits: int | None
    include_merges: bool
    detect_renames: bool
    detect_copies: bool
    include_commit_messages: bool
```

### 4.5 GitHistoryInventory

```python
@dataclass(frozen=True, slots=True)
class GitHistoryInventory:
    repository_namespace: str | None
    scope: GitHistoryScope
    commits: tuple[GitCommit, ...]
    changes: tuple[GitFileChange, ...]
    commit_ids: tuple[str, ...]                 # sorted, same order as commits
    commits_by_id: Mapping[str, GitCommit]
    changes_by_commit: Mapping[str, tuple[GitFileChange, ...]]
    changes_by_path: Mapping[str, tuple[GitFileChange, ...]]
    commits_touching_path: Mapping[str, tuple[str, ...]]
    co_changed_paths: Mapping[str, tuple[str, ...]]
    co_change_incomplete_paths: tuple[str, ...]
    history_is_complete: bool
    incomplete_reasons: tuple[GitIncompleteReason, ...]
    commit_count: int
    change_count: int
    truncated_by_limit: bool
    shallow_boundary_commit_ids: tuple[str, ...]
    commits_with_unavailable_baseline: tuple[str, ...]
    detached_head: bool
    git_version: str | None
```

Invariants:
- `commits` sorted by `(committer_timestamp desc, commit_id asc)`.
- `commit_ids == tuple(c.commit_id for c in commits)`.
- `commit_count == len(commits)`.
- `change_count == len(changes)`.
- Every `GitFileChange.commit_id` references a commit in `commits`.
- Indexes are `MappingProxyType` snapshots built in `__post_init__` from copied
  backing dictionaries; no caller can mutate the published mappings or their
  backing stores. Tuple fields are validated as tuples.
- `commits_with_unavailable_baseline` lists commit IDs for which no first-parent
  tree could be resolved (shallow boundary or otherwise unavailable parent).
  For those commits, `changes_by_commit` is empty and no file-change records
  are fabricated. This distinguishes missing historical change evidence from a
  genuinely unchanged commit (which also has an empty change list but is not
  in this tuple).
- `detached_head` records whether `HEAD` was detached at analysis time. It is
  repository state, not a history-completeness signal, and does not by itself
  make `history_is_complete` false.
- `co_changed_paths[path]` contains sorted unique paths that changed in at
  least one commit also changing `path`, excluding `path` itself. A "path that
  changed" in a commit means a path that is genuinely touched by that commit:
  for a non-copy change, both `old_path` and `new_path` (when present); for a
  `COPIED` change, only `new_path` (the destination). The copy source
  (`old_path` of a `COPIED` change) is lineage evidence and is NOT counted as
  touched by that commit. A commit touching one path contributes no co-change
  entries for that path. A co-change relationship between two distinct paths
  is one unique unordered pair, normalized as `(min(A, B), max(A, B))`; the
  same pair observed in multiple commits counts once toward the global
  budget. The index is symmetric: B appears in `co_changed_paths[A]` if and
  only if A appears in `co_changed_paths[B]`.
- `commits_touching_path[path]` contains sorted unique commit IDs; it
  includes a commit only when `path` is genuinely touched by that commit
  (same rule as above). A copy source path is not added to
  `commits_touching_path` by a copy commit.
- `changes_by_path` keys include both `old_path` and `new_path` where present,
  including copy sources, so callers can look up all changes referencing a
  path. This is a lookup convenience and does not imply the path was touched.
- `co_change_incomplete_paths` is a sorted tuple of paths whose co-change
  partners may have been omitted because the global co-change pair budget was
  exhausted. A path absent from this tuple has complete co-change coverage (its
  partner list is authoritative, including an empty tuple meaning no partners
  observed). A path present in this tuple must NOT be interpreted as having
  no partners; its `co_changed_paths` entry (if present) is partial. A
  missing `co_changed_paths` key combined with absence from
  `co_change_incomplete_paths` is complete absence (never touched by any
  successfully analyzed commit in the selected history); a missing key for a
  path IN this tuple is omitted evidence, not proof of absence. Co-change
  coverage is scoped to successfully analyzed evidence; missing baselines,
  truncation, and commits NOT selected by `path_filter` require callers to
  inspect `incomplete_reasons` and related completeness metadata.
- `history_is_complete` is `True` only when `incomplete_reasons` is empty.

### 4.6 Exceptions

```python
class GitHistoryError(Exception): ...
class GitUnavailableError(GitHistoryError): ...
class GitRepositoryError(GitHistoryError): ...
class GitRevisionError(GitHistoryError): ...
class GitCommandError(GitHistoryError): ...
class GitCommandTimeoutError(GitCommandError): ...
class GitOutputTooLargeError(GitCommandError): ...
class GitParseError(GitHistoryError): ...
class GitInventoryMismatchError(GitHistoryError): ...
class GitConfigurationError(GitHistoryError): ...
```

## 5. Evidence-versus-causality boundary

Module 7 records historical facts only.

Confirmed facts (recorded directly):
- commit parent relationships;
- commit timestamps and author/committer identity;
- file paths touched by a commit;
- change type as reported by Git;
- rename/copy detection with similarity score;
- commit message bytes as recorded.

Heuristics (must be labeled as such in documentation and never stored as facts):
- Git's rename/copy detection is heuristic; `GitFileChange.change_type=RENAMED`
  reflects Git's decision, not semantic proof.
- Co-change groups are temporal correlation, not dependency.

Forbidden invariants:
- Module 7 must not assert that a commit caused a defect.
- Module 7 must not infer logical dependency from co-change.
- Module 7 must not attribute a symbol change to a commit unless the file
  containing that symbol changed in that commit (file-level only).
- Module 7 must not claim a rename preserved semantics.

Unknown or incomplete evidence:
- Missing parent (shallow clone) -> `PARENT_UNAVAILABLE` reason, commit still
  recorded with available parents.
- Missing metadata (an absent `author` or `committer` header) ->
  `MISSING_METADATA`, field set to empty string / 0 with reason recorded.
  Malformed headers (present but unparseable) raise `GitParseError` rather
  than fabricating values.
- Shallow boundary -> `SHALLOW_HISTORY` reason and boundary commit IDs recorded.

Provenance: every record traces to a concrete Git object ID (`commit_id`) and,
for changes, to the commit that produced them. No record exists without a
commit origin.

## 6. Git extraction architecture

Selected approach: **Approach 3 — hybrid: a narrow controlled Git CLI adapter
plus a pure immutable transformation layer.**

- `GitAdapter` (internal) performs all subprocess calls. It is the only
  component that touches `subprocess`. It returns raw text/bytes.
- `GitHistoryParser` (internal, pure) converts raw output into model objects.
- `GitHistoryAnalyzer` orchestrates adapter + parser + index construction.

No external Git library dependency. Standard library only.

### 6.1 GitAdapter safety

Allowed Git commands (read-only, local only):

- `git --version`
- `git rev-parse --is-inside-work-tree` / `--is-bare-repository`
- `git rev-parse --is-shallow-repository`
- `git rev-parse --git-dir` / `--git-common-dir`
- `git rev-parse --verify --end-of-options <rev>^{commit}` / `<rev>^{tree}`
  (including the first-parent form `<rev>^1^{tree}`; the commit's own tree is
  never resolved by a command — it is read from the raw commit object, see 6.4)
- `git symbolic-ref --quiet <revision>` (for detached-HEAD detection)
- `git rev-list ...`
- `git cat-file --batch` (optional, for commit metadata)
- `git diff-tree ...`
- `git log ...` (with explicit format)
- `git config --get remote.origin.url` (optional, for provenance only; not
  collected by default)

Forbidden commands: clone, fetch, pull, push, checkout, reset, rebase, merge,
commit, tag (write), gc, prune, filter-branch, or any command that writes.

Invocation rules:
- Commands are invoked as `git -C <validated_repository_path> ...` with the
  repository path as an argument, not via `cwd` manipulation. The path is
  validated as an existing directory before invocation.
- `subprocess.run`/`Popen` with a list of arguments, `shell=False`.
- `timeout` per command from configuration. Aggregate timeout enforced by
  the analyzer across all commands.
- Capture stdout/stderr with size caps; abort with `GitOutputTooLargeError`
  if exceeded.
- Environment: start from an empty mapping and add only `PATH` (or
  `Path`/`SystemRoot`/`TEMP`/`TMP` as needed for the platform), then set
  `GIT_CONFIG_NOSYSTEM=1`, `GIT_ATTR_NOSYSTEM=1`,
  `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`, `PAGER=cat`,
  `GIT_EXTERNAL_DIFF=`, `GIT_NO_LAZY_FETCH=1`,
  `GIT_NO_REPLACE_OBJECTS=1`,
  `GIT_CONFIG_GLOBAL=<empty file in temp dir>`. All inherited `GIT_*`
  variables (including `GIT_TRACE*`, `GIT_DIR`, `GIT_WORK_TREE`,
  `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`,
  `GIT_PROXY_COMMAND`, `GIT_SSH`, `GIT_ASKPASS`) are dropped, not merely
  `GIT_CONFIG_*`. Proxy variables (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`,
  `NO_PROXY`) are also dropped.
- `core.hooksPath` set to an empty directory via `-c`.
- No network: no `--filter`, no `--depth` changes, no remote operations.
  `GIT_NO_LAZY_FETCH=1` prevents promisor/partial-clone lazy fetches even if
  the repository was cloned with a filter. `--filter` is never passed.
- Temporary helper files (empty global config, empty hooks directory) are
  created in the system temp directory (outside the analyzed repository) using
  `tempfile.TemporaryDirectory` and cleaned up on completion, including on
  error. No helper file is written inside the analyzed repository.
- Option injection defense: revision and path arguments are validated and
  passed after `--end-of-options` / `--`. Arguments starting with `-` are
  rejected unless they are known safe flags constructed internally.
- `--no-ext-diff`, `--no-textconv`, `--no-color`, `--no-abbrev` on diff commands.
- Textconv and external diff are disabled both by flags and by environment
  (`GIT_EXTERNAL_DIFF=`); any attempt to invoke them is a security failure.

### 6.2 Commit enumeration

Commit IDs are enumerated first and authoritatively; metadata is then fetched
for exactly those IDs (see 6.3), so the two queries cannot disagree.

Use `git rev-list` with explicit ordering:

```
git rev-list --topo-order --reverse --max-count=<K> <rev> -- <pathspec...>
```

where `K = max_commits + 1` when `max_commits` is not None, else a very large
value (internal cap). Truncation is determined by lookahead: if more than
`max_commits` IDs are returned, history is truncated (`MAX_COMMITS_REACHED`)
and the extra ID is discarded. With exactly `max_commits` commits, history is
not truncated.

- `--topo-order` ensures deterministic parent-before-child traversal.
- `--reverse` yields oldest-first; the inventory reverses to newest-first
  for output.
- `--max-count` is set to `max_commits + 1` for an exact boundary check, not
  `max_commits`. When `max_commits` is `None`, the effective limit is the
  internal 100,000 cap and the request uses 100,001 for the same exact
  boundary check.
- `include_merges=False` adds `--no-merges`. The resulting inventory is NOT
  full history; `MERGES_EXCLUDED` is added to `incomplete_reasons`.
- Path filter: each validated path is passed as a literal pathspec
  `:(literal)<path>` after `--`, preventing pathspec-magic injection. The
  filter selects commits (commits touching at least one listed path). The
  `changes` list of each selected commit is NOT filtered: it contains every
  file change in that commit. `PATH_FILTER_APPLIED` is added to
  `incomplete_reasons` to record that commit selection was restricted. This
  preserves all actual co-change evidence for selected commits (still subject
  to the global co-change pair budget in 14, reported separately via
  `CO_CHANGE_LIMIT_EXCEEDED`) without falsely claiming unrelated changes were
  filtered out.
- Shallow boundary detected via `git rev-parse --is-shallow-repository`. When
  true, shallow boundary commit IDs are read from the `shallow` file in the
  directory reported by `git rev-parse --git-common-dir` (one object ID per
  line), intersected with the reachable commit set. `--git-common-dir` is used
  so linked worktrees share shallow metadata correctly. The reported directory
  may be relative to the repository root or absolute; it is resolved against
  the validated `repository_path` before the `shallow` file is read, because
  commands run with `git -C <path>` and no cwd manipulation. `SHALLOW_HISTORY`
  and `PARENT_UNAVAILABLE` are recorded when any reachable commit has a
  missing parent.

### 6.3 Commit metadata

Metadata is fetched for exactly the commit IDs returned by 6.2, using
`git cat-file --batch` with the raw commit objects. This guarantees the
metadata set is identical to the selected commit set, independent of any
`git log` selection logic.

Command:

```
git cat-file --batch
```

with the commit IDs written to stdin (one per line), preceded by
`git -C <repo>`. Each response has the form:

```
<oid> <type> <size>\n<raw object bytes>\n
```

The raw commit object is parsed as:

```
tree <tree_id>
parent <parent_id>       (zero or more, in Git order)
author <name> <email> <timestamp> <tz>
committer <name> <email> <timestamp> <tz>
encoding <encoding>      (optional)
gpgsig ...               (optional, multi-line)

<message>
```

- Parent IDs are taken from `parent` lines in their original order. For shallow
  boundary commits where `git log %P` would report none, the raw object's
  `parent` lines are authoritative; if the parent object is unavailable, the
  ID is retained and `PARENT_UNAVAILABLE` is recorded. A raw object with no
  `parent` lines is a genuine root.
- Timestamps/tz are parsed from the `author`/`committer` lines. The message
  is the bytes after the blank line, decoded per the declared encoding (or
  UTF-8 if no encoding header), then normalized to a Python `str`. If
  `include_commit_messages=False`, `message` is set to `None`.
- A commit with no `author` or `committer` line records `MISSING_METADATA`
  (fields empty/0) and is non-fatal. Malformed required headers (present but
  unparseable) raise `GitParseError`.
- Shallow metadata uses `git rev-parse --git-common-dir` to locate the shared
  `shallow` file in linked worktrees.

### 6.4 File changes

File changes are extracted by comparing the commit tree against exactly one
baseline tree, chosen per commit type:

- Root commit: baseline = empty tree
  (`4b825dc642cb6eb9a060e54bf8d69288fbee4904` for SHA-1 repos); compare
  empty tree vs. the commit's tree.
- Non-root, non-merge commit: baseline = first parent's tree; compare first
  parent tree vs. the commit's tree.
- Merge commit: baseline = first parent's tree; compare first parent tree vs.
  the merge commit's tree. Only first-parent changes are emitted. Other
  parents' changes are not reported. `MERGE_SEMANTICS_PARTIAL` is added to
  `incomplete_reasons` whenever any merge commit is present.

The comparison uses two tree objects, never `git diff-tree <commit>` alone,
because the latter silently omits merge commits and can mix parents.

Commands:

1. Resolve trees:
   - commit tree: the `tree <tree_id>` header parsed and validated from the
     raw commit object fetched in 6.3 via `git cat-file --batch`. No Git
     command is needed; the tree ID travels with the metadata batch.
   - first-parent tree (non-root): one separate verified command,
     `git rev-parse --verify --end-of-options <commit>^1^{tree}`. A
     `rev-parse --verify` invocation must never receive more than one
     revision argument.
   - root baseline: the well-known empty tree ID
     (`4b825dc642cb6eb9a060e54bf8d69288fbee4904` for SHA-1;
     `6ef19b41225c5369f1c104d45d8d85efa9b057b53b14b4b9b939dd74decc5321`
     for SHA-256 repos). Both are fixed constants, never computed at
     runtime; no Git command is needed to obtain them. The applicable
     constant is selected by object format (commit-ID length: 40 hex =
     SHA-1, 64 hex = SHA-256).
2. Diff:
   ```
   git diff-tree --no-commit-id -r -z --no-ext-diff --no-textconv \
     --no-color --name-status <baseline_tree> <commit_tree> \
     [--find-renames] [--find-copies-harder]
   ```
   `--no-commit-id` suppresses the commit-ID header; the two-tree form has no
   header, but the flag is kept for safety. `--root` is not used because the
   empty-tree baseline handles roots explicitly.

Parse NUL-delimited records (each record ends with a NUL; paths are NUL-
terminated, not newline-terminated):
- `A\0<path>\0` -> ADDED
- `M\0<path>\0` -> MODIFIED
- `D\0<path>\0` -> DELETED
- `T\0<path>\0` -> TYPE_CHANGED
- `U\0<path>\0` -> UNMERGED
- `R<score>\0<old>\0<new>\0` -> RENAMED
- `C<score>\0<old>\0<new>\0` -> COPIED

`--find-renames`/`--find-copies-harder` use Git's default similarity threshold (50%).
Rename detection is heuristic and documented as such.

Rename/copy flags are passed conditionally per 6.5. Copy detection uses
`--find-copies-harder` so that copies of files that are unchanged in the
commit are detected (plain `--find-copies` only inspects files modified in
the same changeset). When `detect_copies=True` and `detect_renames=False`,
pass `--no-renames --find-copies-harder`. When both are false, pass neither.
When `detect_renames=True`, pass `--find-renames` (plus
`--find-copies-harder` when `detect_copies=True`).

Merge commits: only first-parent diff is recorded; `MERGE_SEMANTICS_PARTIAL`
reason is added to `incomplete_reasons` when merges are present.

Unavailable comparison baseline (shallow or otherwise):
- The single first-parent-tree resolution command (`<commit>^1^{tree}`) fails
  when the first parent's tree is unavailable (shallow boundary or missing
  parent object). No file-change records are fabricated for that commit.
  `changes_by_commit[commit_id]` is an empty tuple. The commit is
  retained in `commits` with its raw parent IDs and `is_root == False` (a
  shallow boundary has raw `parent` lines and is never reclassified as a
  root). `SHALLOW_HISTORY` and `PARENT_UNAVAILABLE` are added to
  `incomplete_reasons` when the unavailable parent belongs to a recognized
  shallow boundary; if a required parent or tree is unavailable outside a
  shallow boundary (for example a missing object), the same omission behavior
  applies with `PARENT_UNAVAILABLE` recorded and no `SHALLOW_HISTORY`. The
  commit ID is appended to `commits_with_unavailable_baseline`.
- The commit is NOT compared against the empty tree, so a shallow boundary
  commit does not appear to add every file.
- Missing historical change evidence is thus distinguishable from a genuinely
  unchanged commit: the latter has an empty change list and is absent from
  `commits_with_unavailable_baseline`.
- Resolution failure is classified by exit status, never by stderr text: a
  root commit, a shallow boundary, and an unknown object ID all produce the
  identical `fatal: Needed a single revision` message. A `rev-parse --verify`
  invocation that exits 128 for a commit ID known to exist is the documented
  non-fatal "baseline unavailable" outcome. Every other failure (timeout,
  oversize output, a different exit status, or termination by signal) raises
  `GitCommandError` per 4.6/15.

### 6.5 Rename/copy similarity

`similarity` is the integer score (0-100) reported by Git. Rename/copy flags
are passed to Git conditionally (see 6.4), so no client-side normalization is
required: with `detect_renames=False` Git reports delete+add; with
`detect_copies=False` Git reports add. `similarity` is `None` unless the
emitted change type is `RENAMED` or `COPIED`. Git's default similarity
threshold (50%) applies.

## 7. Deterministic parsing and ordering

- Commit output order: `(committer_timestamp desc, commit_id asc)`.
- Parent IDs in Git's original order (not sorted; first parent first).
- Changes within a commit sorted by `(change_type value, new_path or old_path)`.
- `changes_by_path` lists sorted by `(committer_timestamp desc, commit_id asc)`
  of the containing commit, then change order.
- `co_changed_paths[path]` sorted ascending, deduplicated.
- `commits_touching_path[path]` sorted ascending.
- All string comparisons use code point order (Python default).
- NUL-delimited parsing prevents locale-dependent formatting.
- No use of `git log --pretty` human format.

### 7.1 Parsing and path identity policy

- Invalid UTF-8 in commit metadata or paths is not silently replaced.
  - Commit metadata is obtained via `git cat-file --batch` as raw object bytes
    and decoded explicitly per 6.3; if a byte sequence cannot be decoded as
    UTF-8, `GitParseError` is raised with the offending byte offset. No
    replacement characters are introduced.
  - Paths are decoded as UTF-8 with `errors='strict'`. A path containing
    invalid UTF-8 raises `GitParseError` whose message includes the hex of
    the offending bytes. Lossy `surrogateescape`/`replace` decoding is not
    used, because it risks identity collisions.
  - A repository whose history contains non-UTF-8 paths cannot be analyzed by
    v1; this is a documented limitation, not a silent degradation.
- NUL-delimited record boundaries: records are split on NUL bytes. A record
  with an unexpected field count raises `GitParseError`. Trailing empty
  records are ignored; a NUL inside a path is impossible because NUL is the
  delimiter.
- Commit message encoding: the raw commit object's `encoding` header (default
  UTF-8) is honored; the message is transcoded to a Python `str`. If
  transcoding fails, `GitParseError` is raised.
- Canonical path identity: paths are stored as Python `str` (validated
  UTF-8). Backslash is not a separator in Git's stored paths; a path
  containing a backslash is a literal filename component and is rejected by
  path validation (paths use forward slashes only). `change_id` serializes
  paths as JSON strings with `ensure_ascii=False`, so Unicode is preserved
  deterministically.
- Commit IDs are validated as exactly 40 or 64 lowercase hexadecimal
  characters. Uppercase or other lengths raise `GitRevisionError`/`GitParseError`.

## 8. Commit DAG semantics

- Full commit IDs (40 or 64 hex).
- Parent list from `%P`.
- Root commits have empty parent list.
- Merge commits have >1 parent.
- Shallow clones: missing parents are recorded in
  `shallow_boundary_commit_ids`; `SHALLOW_HISTORY` and `PARENT_UNAVAILABLE`
  reasons are added. Parent IDs are recovered from the raw commit object via
  `git cat-file --batch` (see 6.3), not from `git log %P`, which can report
  an empty parent list for a shallow boundary even when the raw object has a
  `parent` line. A raw object with no `parent` lines is a genuine root. A raw
  object with `parent` lines whose objects are unavailable is a shallow
  boundary, not a root. `--git-common-dir` locates shared shallow metadata in
  linked worktrees.
- Detached HEAD is repository state, not a history property. It is recorded
  via the `detached_head: bool` field of `GitHistoryInventory` (see 4.5) and
  does not add an incomplete reason and does not affect `history_is_complete`.

- Concurrent mutation: if Git commands fail mid-analysis, raise
  `GitCommandError`; partial results are not returned.

## 9. File change and rename semantics

- Paths are repository-relative POSIX paths.
- Rename detection is Git's heuristic; similarity score recorded.
- A rename with similarity < threshold is reported as delete+add by Git; the
  analyzer records what Git reports.
- Large diffs: `--numstat` not used; name-status only. No diff content. Diff
  statistics (additions/deletions/binary status) are out of scope for v1.
- Merge commits: first-parent diff only; `MERGE_SEMANTICS_PARTIAL` reason.
- Copy source is lineage, not a change: a `COPIED` change records the source
  in `old_path` and the destination in `new_path`. The source path is NOT
  reported as modified by that commit, is NOT added to `commits_touching_path`,
  and is NOT a co-change partner. Tests must prove a copy source is not falsely
  reported as modified or co-changed.
- Rename/copy flag compatibility: `detect_renames` and `detect_copies` are
  passed to Git independently as `--find-renames` / `--find-copies-harder` (with
  `--no-renames` when renames are disabled but copies enabled). When both are
  false, neither flag is passed. Git's copy detection may still require
  rename machinery internally; the analyzer does not normalize client-side.

## 10. Evidence provenance and identity

- Every `GitCommit` is identified by its Git object ID. No synthesized ID.
- Every `GitFileChange` has a `change_id` derived from canonical JSON of
  `(commit_id, change_type, old_path, new_path)` with namespace prefix
  `git-history:change:v1`. This allows deduplication and stable references.
- No Module 6 identity namespace is reused.
- `GitHistoryInventory` has no single identity; it is a container. Its
  provenance is the set of commit IDs plus scope.

## 11. Incomplete/ambiguous history semantics

- `history_is_complete` is `True` only when `incomplete_reasons` is empty.
  Reasons that make it false: `SHALLOW_HISTORY`, `PARENT_UNAVAILABLE`,
  `MAX_COMMITS_REACHED`, `MERGE_SEMANTICS_PARTIAL`, `MERGES_EXCLUDED`,
  `MISSING_METADATA`, `PATH_FILTER_APPLIED`, `CO_CHANGE_LIMIT_EXCEEDED`.
  `OUTPUT_TOO_LARGE` and `COMMAND_TIMEOUT` are NOT incomplete reasons:
  those conditions raise `GitOutputTooLargeError` / `GitCommandTimeoutError`
  and no inventory is returned. `detached_head` is not a reason and does not
  affect completeness.
- Operational failures (timeout, oversize output, command failure, parse
  failure) raise exceptions and return no inventory. Truncation by
  `max_commits`, shallow history, merge semantics, merge exclusion, missing
  metadata, path filtering, and co-change limits are non-fatal and produce an
  inventory with the corresponding reason.
- `path_filter` applied sets `PATH_FILTER_APPLIED` reason (this is expected,
  not an error).
- `max_commits` truncation sets `MAX_COMMITS_REACHED`.
- Missing parent (shallow) sets `SHALLOW_HISTORY` and `PARENT_UNAVAILABLE`.

## 12. Integration with Modules 1-6

- Module 1: `RepositoryInfo.local_path` is the primary input path.
  `RepositoryInfo.current_commit` can be used as default `revision`.
- Module 2: optional `FileInventory` for namespace and path validation.
  Module 7 validates that changed paths are plausible but does not require
  them to be in the inventory (deleted files won't be).
- Module 6: no direct dependency. Callers map `GitFileChange.new_path`/
  `old_path` to `CodeGraphInventory.nodes_by_file` keys (relative POSIX paths)
  to associate commits with graph nodes. This is a file-level association only.
  Symbol-level attribution is out of scope (would require parsing historical
  source).
- No upstream module is modified.

## 13. Security and privacy

- All subprocess calls use argument lists, `shell=False`.
- Environment sanitized (see 6.1).
- No network operations.
- No checkout/mutation of the analyzed repository.
- No execution of analyzed source.
- No external diff tools or textconv (disabled by flag and environment).
- No hooks execution (empty hooks path).
- No lazy fetch in promisor/partial-clone repositories (`GIT_NO_LAZY_FETCH=1`).
- No replacement objects: `GIT_NO_REPLACE_OBJECTS=1` is set for every Git
  invocation, so history traversal (`rev-list`), raw object reading
  (`git cat-file --batch`), revision and tree resolution (`rev-parse`), and
  tree/diff operations (`diff-tree`) all read the real requested objects, not
  substituted content from `refs/replace/`. Commit metadata and parent
  ordering come from the actual raw object, and replacement refs never alter
  the published inventory. Module 7 never invokes `git replace` or modifies
  replacement refs. Offline tests use a temporary repository with a
  replacement ref to verify the analyzer sees the original commit message,
  parents, and tree.
- No trace output (`GIT_TRACE*` removed).
- No repository overrides (`GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`,
  `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES` removed).
- No executable helpers (askpass/ssh/proxy command variables removed).
- Timeouts per command.
- Output size caps.
- Path/revision validation prevents option injection.
- Requires Git >= 2.29 (a conservative floor; `--end-of-options` landed in Git
  2.24 and `--is-shallow-repository` in 2.15);
  older versions raise `GitUnavailableError`/`GitCommandError`.
- Commit messages treated as untrusted data (stored, never executed or
  interpreted).
- Error messages must not include environment variables, credentials, or
  remote URLs beyond what is necessary. Remote URL is not collected by default.
- Secrets in historical commits are not redacted (Module 7 stores facts; redaction
  is a Module 10 concern). Document this.

## 14. Resource bounds and scaling

- `max_commits` bounds commit count (default None = all, internal cap 100000).
- Per-command timeout (default 30s). Aggregate analysis timeout (default 300s)
  enforced across all commands; exceeding raises `GitCommandTimeoutError`.
- Output cap (`max_output_bytes`, default 50MB) enforced DURING reading:
  stdout/stderr are read in fixed-size chunks (e.g. 64 KiB) via
  `subprocess.Popen`; if the accumulated bytes for that command exceed the
  cap, the process is terminated and `GitOutputTooLargeError` is raised. No
  post-hoc size check on an already-captured buffer. Process cleanup uses
  `terminate()` then `kill()` after a short grace period, with `wait()`; works
  on Windows and POSIX. stdout and stderr are read through concurrent reader
  threads to avoid Windows pipe deadlock. `max_output_bytes` is the per-command
  cap.
- Cumulative output budget: stdout and stderr bytes read across all commands
  are accumulated in a running counter (fixed internal default cap 256 MiB,
  not configurable). The counter is checked after each chunk; if exceeded,
  the current process is terminated with the same terminate/kill/wait cleanup
  as the per-command cap and `GitOutputTooLargeError` is raised. This
  prevents many small commands from bypassing the per-command cap. Raw
  buffers are released after parsing (no retention beyond the current
  command; nothing raw is accumulated into the inventory).
- Global co-change pair budget: total materialized co-change pairs across
  all commits are capped (fixed internal default 10,000,000, not
  configurable). Accounting unit: a co-change relationship between two
  distinct paths A and B is exactly one unique unordered pair, normalized
  as `(min(A, B), max(A, B))`; the same pair observed in multiple commits
  counts once. The `co_changed_paths` index exposes each relationship
  symmetrically: B is a partner of A if and only if A is a partner of B.
  The two symmetric index entries produced by one pair are a single budget
  unit; the budget counts pairs, not index entries.
  The per-commit cap below is insufficient on its own, because many commits
  below that cap can still produce an enormous total number of pairs.
  Commits are processed in inventory order (newest-first). For each commit,
  its pair contribution is the number of NEW unique normalized pairs (not
  already materialized from earlier-processed commits) it would introduce.
  The whole contribution is tested against the remaining budget BEFORE any
  mutation of the accumulated index; if it does not fit, the commit's
  co-change pairs are omitted entirely (atomic per commit) and all of its
  genuinely touched paths are appended to `co_change_incomplete_paths`.
  `CO_CHANGE_LIMIT_EXCEEDED` is added to `incomplete_reasons` if any commit
  was skipped. Ordinary file-change and commit indexes are unaffected.
  Co-change coverage is scoped to successfully analyzed evidence. Commits
  listed in `commits_with_unavailable_baseline` are not analyzed for changes
  and contribute no co-change pairs; commits excluded by `max_commits`
  truncation (inventory-level `truncated_by_limit` / `MAX_COMMITS_REACHED`)
  and commits not selected by `path_filter`
  (`PATH_FILTER_APPLIED`) are likewise not analyzed. Commits *selected* by
  `path_filter` **are** analyzed in full — their complete first-parent change
  sets and co-change pairs are materialized subject to the budget (see 6.2).
  Callers must consult `incomplete_reasons` and related completeness metadata
  rather than treat an empty or absent partner list as evidence about
  unanalyzed commits.
- Copy detection uses `--find-copies-harder`, whose cost is proportional to the
  number of files in the compared trees; this is the dominant per-commit cost
  when `detect_copies=True`.
- Total subprocess invocations are bounded by a small constant plus O(commits):
  repository validation and revision resolution, one rev-list, one cat-file
  batch, one diff-tree per commit, and at most one tree-resolution command per
  non-root commit (the commit tree itself comes from the raw commit object).
  No per-symbol or per-reference commands.
- `co_changed_paths` is built with a per-commit path-count cap (default
  1000 distinct touched paths). For a commit touching more than the cap, its
  co-change pairs are not materialized, all of its genuinely touched paths are
  appended to `co_change_incomplete_paths`, and `CO_CHANGE_LIMIT_EXCEEDED` is
  added to `incomplete_reasons`. This avoids unbounded O(k^2)
  materialization. `changes_by_path` and `commits_touching_path` are still
  populated for that commit (they are O(k)).
- Maximum accumulated evidence size: `changes` tuple is capped at 1,000,000
  entries; exceeding raises `GitOutputTooLargeError` (no partial inventory).
- Indexed lookups: `commits_by_id`, `changes_by_commit`, `changes_by_path`,
  `commits_touching_path`, `co_changed_paths` all O(1) average.
- Deterministic operation-count tests: command count proportional to commits,
  not symbols; co-change materialization bounded.

## 15. Error handling

- Missing git executable -> `GitUnavailableError`.
- Not a git repository -> `GitRepositoryError`.
- Invalid revision -> `GitRevisionError`.
- Command failure -> `GitCommandError` with sanitized stderr.
- Timeout -> `GitCommandTimeoutError`.
- Output too large -> `GitOutputTooLargeError` (also raised when the
  1,000,000-change evidence cap is exceeded; see 14).
- Parse failure -> `GitParseError`.
- Inventory mismatch -> `GitInventoryMismatchError`.
- Configuration error -> `GitConfigurationError`.
- Partial results are never returned on error; analyzer raises.

## 16. Offline testing strategy

### Unit tests
- Model validation and invariants.
- Canonical change_id computation.
- Ordering and immutability.
- Git output parsing (NUL-delimited, renames, copies, binary, unicode).
- Error classification.
- History limits and truncation metadata.

### Temporary local Git repository tests
- Root commit, linear commits, multiple changed files.
- Renames and deletions.
- Merge commit where each parent has distinct changes (first-parent proof).
- Root diff-tree output format (empty-tree baseline).
- True root versus shallow boundary.
- Linked worktree with shared shallow metadata (`--git-common-dir`).
- Preserved parent ordering (first parent first).
- Exact `max_commits` boundary (N commits not truncated; N+1 truncated).
- Excluded merge scope (`include_merges=False`).
- Path-filtered commit with unrelated file changes (commit selection only;
  changes list unfiltered; `PATH_FILTER_APPLIED` recorded).
- Literal Git pathspec handling (paths with bracket-style pathspec magic such
  as `[a-z]`; `*` and `?` are illegal filename characters on Windows and are
  covered by a POSIX-only test).
- Copy source unchanged (source not modified/co-changed).
- Huge single-commit co-change cardinality (co-change cap).
- Single-revision `rev-parse --verify` invocations only (the adapter never
  passes two revision arguments; the commit tree comes from the raw commit
  object header).
- Co-change pair accounting: unique unordered normalized pairs; the same pair
  in multiple commits counts once toward the global budget; a commit's
  contribution is its NEW unique pairs; the budget is checked before any
  index mutation; overflow omission is atomic per commit.
- Co-change coverage scoping: missing baselines, truncation, and commits
  excluded by `path_filter` require callers to inspect completeness metadata.
- Aggregate subprocess/output/time limits.
- Many small commands whose combined output exceeds the cumulative output
  budget (global cap enforced; staying under the per-command cap does not
  bypass it).
- Malicious Git environment/configuration.
- Lazy-fetch prevention.
- Replacement-ref repository (analyzer sees original commit, parents, tree;
  never invokes `git replace`).
- Malformed UTF-8/path handling.
- Depth-one shallow clone with unavailable parent tree (boundary commit
  retained, not classified as root, no fabricated changes,
  `SHALLOW_HISTORY` + `PARENT_UNAVAILABLE`,
  `commits_with_unavailable_baseline` populated).
- Detached HEAD.
- Empty repository.
- Shallow history (via `git clone --depth 1 file:///<local-repo-path> <dir>`;
  a plain local path ignores `--depth` and yields a full clone).
- Many moderate-sized commits exhausting the global co-change pair budget
  (`CO_CHANGE_LIMIT_EXCEEDED`, deterministic `co_change_incomplete_paths`,
  `history_is_complete` is false).
- One oversized commit exceeding the global budget (atomic omission of its
  co-change pairs; no partial partners).
- Co-change omission leaves ordinary file-change and commit indexes unchanged.
- Path-filtered commit modifying both a filtered path and an unrelated path
  (commit selection only; changes list unfiltered; `PATH_FILTER_APPLIED`
  recorded; co-change evidence retained subject to the co-change budget).
- Filenames with spaces and Unicode.
- Same timestamps, distinct parents.
- Bounded history (max_commits).

### Security tests
- Shell injection prevention (revision/path starting with `-`, NUL, `..`).
- No network (monkeypatch subprocess to fail on network commands; assert only
  allowed commands invoked).
- No mutation (hash repository state before/after).
- No hooks/textconv/external-diff execution (empty hooks dir; assert no external
  command and `--no-textconv` present).
- Detached HEAD recorded as boolean, not a crash.
- Subprocess timeouts.
- Malformed Git metadata.
- Safe error messages (no env, credentials, or remote URL leak).
- Empty repository returns empty inventory.

### Integration tests
- Real Module 1 `RepositoryInfo` path.
- File inventory association.
- Module 6 graph path association (file-level, no graph mutation).
- Deterministic output on repeat runs.

### Performance tests
- Command count proportional to commits.
- Indexed lookup O(1).
- No hidden quadratic scans.

All tests offline, no network, no GitHub access.

## 17. Explicit exclusions

Module 7 must NOT implement:
- Module 8 runtime evidence.
- Module 9 hybrid RAG/retrieval.
- Module 10 Gemini causal reasoning.
- UI/frontend.
- GitHub API or remote fetching.
- Automatic repository modification.
- Git checkout/reset/rebase/merge.
- Source code execution.
- Compiler/runtime instrumentation.
- Speculative blame or root-cause claims.
- Rebuilding Module 6 code graphs from history.
- Full historical semantic code parsing.
- Symbol-level change attribution (file-level only).

Retain all Modules 1-6 production behavior. Do not refactor upstream modules.

## 18. Alternatives and tradeoffs

### Approach 1: Controlled Git CLI with narrow adapter
- Pros: no new dependency, uses system git (already required by Module 1),
  full access to porcelain/plumbing, well-tested.
- Cons: subprocess management burden, parsing complexity, version differences.
- Verdict: viable, but extraction and transformation are coupled unless split.

### Approach 2: Python Git library (GitPython/pygit2)
- Pros: typed API, less parsing.
- Cons: new dependency (violates current policy unless justified), security
  surface of library, pygit2 requires libgit2 binaries (Windows friction),
  GitPython shells out anyway.
- Verdict: rejected for MVP; dependency footprint and Windows compatibility.

### Approach 3: Hybrid — narrow CLI adapter + pure immutable transformation (SELECTED)
- Extraction isolated behind `GitAdapter` (only subprocess user).
- Transformation is pure functions over raw output -> frozen models.
- Testable independently (parser unit tests with fixture output).
- Preserves security boundary and deterministic output.
- No new dependency.

Selected: Approach 3.

## 19. Resolved design decisions

1. `detached_head` is stored as `detached_head: bool` on `GitHistoryInventory`
   (not in scope, not as an incomplete reason). Resolved.
2. Default `max_commits`: `None` (all) with internal cap 100000. Resolved.
3. `detect_copies` defaults to `False`. Resolved.
4. Timestamp representation: epoch int + timezone string. Resolved.
5. Change-type diff statistics (`additions`/`deletions`/`is_binary`) are out of
   scope for v1; extraction is name-status only. Resolved.
6. `detect_renames`/`detect_copies` flags are passed to Git conditionally; no
   client-side normalization. Resolved.

## 20. Acceptance criteria

Module 7 is complete when:

1. `GitHistoryAnalyzer.analyze` produces a `GitHistoryInventory` for a local
   Git repository.
2. All models are frozen/slotted with immutable indexes.
3. Commit DAG, file changes, renames/copies recorded with provenance.
4. Deterministic output on repeat runs (same commit order, same change order).
5. Shallow/truncated history reported via `incomplete_reasons`.
6. Security tests pass (no network, no mutation, no injection, no hooks).
7. Resource bounds enforced (max_commits, per-command timeout, per-command
   output cap, cumulative output budget, co-change pair budget).
8. Offline tests pass with temporary local repos.
9. No upstream module modified.
10. No Modules 8-10 functionality.
11. Independent review approved with no Critical/Important findings.
12. A depth-one shallow clone and a linked worktree are covered: boundary
    commits are retained with raw parent IDs, never classified as root, never
    compared against the empty tree, and produce no fabricated file-change
    records; `SHALLOW_HISTORY`/`PARENT_UNAVAILABLE` and
    `commits_with_unavailable_baseline` are populated.
13. The cumulative output budget is enforced across the entire analysis; many
    small commands cannot bypass it, and raw buffers are not retained.
14. The global co-change pair budget produces deterministic atomic per-commit
    omission with explicit `co_change_incomplete_paths` reporting;
    `history_is_complete` is false when a budget is hit; ordinary
    file-change/commit indexes are unaffected.
15. `path_filter` selects commits only; every selected commit retains its full
    first-parent file-change set, including unrelated paths.

---

## Appendix A: Proposed package layout

```text
backend/git_history/
  __init__.py
  analyzer.py
  adapter.py
  models.py
  parser.py
  identities.py
  exceptions.py
  invariants.py
tests/
  test_git_history_models.py
  test_git_history_identities.py
  test_git_history_parser.py
  test_git_history_analyzer.py
  test_git_history_security.py
  test_git_history_integration.py
  test_git_history_scaling.py
```
