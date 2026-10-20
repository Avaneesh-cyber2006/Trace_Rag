"""Analyzer orchestration for Module 7."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Mapping

from .adapter import GitAdapter, BaselineResolution
from .exceptions import (
    GitConfigurationError,
    GitInventoryMismatchError,
    GitOutputTooLargeError,
    GitParseError,
    GitRepositoryError,
    GitRevisionError,
)
from .identities import compute_change_id
from .invariants import EMPTY_TREE_SHA1, EMPTY_TREE_SHA256
from .models import (
    GitChangeType,
    GitCommit,
    GitFileChange,
    GitHistoryInventory,
    GitHistoryScope,
    GitIncompleteReason,
)
from .parser import RawCommitObject, parse_name_status

MAX_CHANGES = 1_000_000
CO_CHANGE_PAIR_BUDGET = 10_000_000
CO_CHANGE_PER_COMMIT_PATH_CAP = 1000


@dataclass(frozen=True, slots=True)
class CoChangeResult:
    co_changed_paths: dict
    incomplete_paths: tuple
    limit_exceeded: bool


def touched_paths(commit_id: str, changes: Sequence) -> tuple[str, ...]:
    paths = set()
    for ch in changes:
        if ch.change_type == GitChangeType.COPIED:
            if ch.new_path:
                paths.add(ch.new_path)
        else:
            if ch.old_path:
                paths.add(ch.old_path)
            if ch.new_path:
                paths.add(ch.new_path)
    return tuple(sorted(paths))


def build_commits_by_id(commits):
    return {c.commit_id: c for c in commits}


def build_changes_by_commit(commits, changes):
    result = {c.commit_id: [] for c in commits}
    for ch in changes:
        result.setdefault(ch.commit_id, []).append(ch)
    for cid in result:
        grp = sorted(result[cid], key=lambda c: (c.change_type.value, c.new_path or c.old_path or ""))
        result[cid] = tuple(grp)
    return result


def build_changes_by_path(changes, commits_by_id):
    result = {}
    for order, ch in enumerate(changes):
        for p in (ch.old_path, ch.new_path):
            if p is None:
                continue
            result.setdefault(p, []).append((order, ch))
    # sort by committer timestamp desc, commit_id asc, then change order
    for p in result:
        def key(item):
            order, ch = item
            c = commits_by_id.get(ch.commit_id)
            ts = c.committer_timestamp if c is not None else 0
            return (-ts, ch.commit_id, order)
        result[p].sort(key=key)
        result[p] = tuple(ch for _order, ch in result[p])
    return result


def build_commits_touching_path(changes):
    result = {}
    for ch in changes:
        # genuinely touched: copy -> new only; others -> old+new
        paths = set()
        if ch.change_type == GitChangeType.COPIED:
            if ch.new_path:
                paths.add(ch.new_path)
        else:
            if ch.old_path:
                paths.add(ch.old_path)
            if ch.new_path:
                paths.add(ch.new_path)
        for p in paths:
            result.setdefault(p, set()).add(ch.commit_id)
    for p in result:
        result[p] = tuple(sorted(result[p]))
    return result


def build_co_change_index(commits, changes_by_commit, *, per_commit_path_cap=None, global_pair_budget=None):
    if per_commit_path_cap is None:
        per_commit_path_cap = CO_CHANGE_PER_COMMIT_PATH_CAP
    if global_pair_budget is None:
        global_pair_budget = CO_CHANGE_PAIR_BUDGET
    # inventory order newest-first
    ordered = sorted(commits, key=lambda c: (-c.committer_timestamp, c.commit_id))
    materialized_pairs = set()
    index = {}  # path -> set of partners
    incomplete_paths_set = set()
    limit_exceeded = False
    remaining_budget = global_pair_budget
    for commit in ordered:
        chs = changes_by_commit.get(commit.commit_id, ())
        tp = touched_paths(commit.commit_id, chs)
        if len(tp) > per_commit_path_cap:
            for p in tp:
                incomplete_paths_set.add(p)
            limit_exceeded = True
            continue
        # candidate pairs
        pairs = set()
        for i in range(len(tp)):
            for j in range(i + 1, len(tp)):
                a, b = tp[i], tp[j]
                pairs.add((a, b) if a < b else (b, a))
        new_pairs = pairs - materialized_pairs
        if len(new_pairs) > remaining_budget:
            for p in tp:
                incomplete_paths_set.add(p)
            limit_exceeded = True
            continue
        # apply
        materialized_pairs |= new_pairs
        remaining_budget -= len(new_pairs)
        for a, b in new_pairs:
            index.setdefault(a, set()).add(b)
            index.setdefault(b, set()).add(a)
    # convert sets to sorted tuples
    co_changed = {}
    for p, partners in index.items():
        co_changed[p] = tuple(sorted(partners))
    return CoChangeResult(co_changed, tuple(sorted(incomplete_paths_set)), limit_exceeded)


class GitHistoryAnalyzer:
    def __init__(self, *, git_executable="git", command_timeout_seconds=30.0,
                 max_output_bytes=50 * 1024 * 1024, max_commits=None):
        if not isinstance(git_executable, str) or not git_executable:
            raise GitConfigurationError("git_executable must be nonempty")
        if not isinstance(command_timeout_seconds, (int, float)) or isinstance(command_timeout_seconds, bool) or command_timeout_seconds <= 0:
            raise GitConfigurationError("command_timeout_seconds must be > 0")
        if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes <= 0:
            raise GitConfigurationError("max_output_bytes must be > 0")
        if max_commits is not None and (not isinstance(max_commits, int) or isinstance(max_commits, bool) or max_commits <= 0):
            raise GitConfigurationError("max_commits must be positive")
        self.git_executable = git_executable
        self.command_timeout_seconds = command_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_commits = max_commits

    def analyze(self, repository_path, *, repository_namespace=None, file_inventory=None,
               revision="HEAD", max_commits=None, include_merges=True, detect_renames=True,
               detect_copies=False, path_filter=(), include_commit_messages=True):
        adapter = GitAdapter(
            git_executable=self.git_executable,
            command_timeout_seconds=self.command_timeout_seconds,
            max_output_bytes=self.max_output_bytes,
            max_commits=self.max_commits,
        )
        try:
            return self._analyze(
                adapter,
                repository_path,
                repository_namespace=repository_namespace,
                file_inventory=file_inventory,
                revision=revision,
                max_commits=max_commits,
                include_merges=include_merges,
                detect_renames=detect_renames,
                detect_copies=detect_copies,
                path_filter=path_filter,
                include_commit_messages=include_commit_messages,
            )
        finally:
            adapter.close()

    def _analyze(self, adapter, repository_path, *, repository_namespace=None, file_inventory=None,
                 revision="HEAD", max_commits=None, include_merges=True, detect_renames=True,
                 detect_copies=False, path_filter=(), include_commit_messages=True):
        rp = Path(repository_path)
        if not rp.exists() or not rp.is_dir():
            raise GitRepositoryError("not a repository")
        # namespace handling
        if repository_namespace is not None:
            if not isinstance(repository_namespace, str) or repository_namespace == "":
                raise GitConfigurationError("invalid namespace")
        if file_inventory is not None:
            inv_ns = getattr(file_inventory, "repository_namespace", None)
            if repository_namespace is None:
                repository_namespace = inv_ns
            else:
                if inv_ns is not None and inv_ns != repository_namespace:
                    raise GitInventoryMismatchError("repository namespace mismatch")
            # validate repository_path
            inv_path = getattr(file_inventory, "repository_path", None)
            if inv_path is not None:
                if Path(inv_path).resolve() != rp.resolve():
                    raise GitInventoryMismatchError("repository path mismatch")
        if repository_namespace is not None and (not isinstance(repository_namespace, str) or repository_namespace == ""):
            raise GitConfigurationError("invalid namespace")

        # validate max_commits arg
        if max_commits is not None and (not isinstance(max_commits, int) or isinstance(max_commits, bool) or max_commits <= 0):
            raise GitConfigurationError("max_commits must be positive")
        for flag_name, flag_val in (
            ("include_merges", include_merges),
            ("detect_renames", detect_renames),
            ("detect_copies", detect_copies),
            ("include_commit_messages", include_commit_messages),
        ):
            if not isinstance(flag_val, bool):
                raise GitConfigurationError(f"{flag_name} must be bool")

        # validate path_filter
        pf = list(path_filter)
        validated_pf = []
        for p in pf:
            if not isinstance(p, str) or p == "" or "\x00" in p or "\\" in p:
                raise GitConfigurationError("invalid path filter")
            if p.startswith("/"):
                raise GitConfigurationError("invalid path filter")
            parts = p.split("/")
            if any(part in ("", ".", "..") for part in parts):
                raise GitConfigurationError("invalid path filter")
            validated_pf.append(p)
        validated_pf = sorted(set(validated_pf))

        # inspect repo
        ctx = adapter.inspect_repository(rp)
        git_version = ctx.git_version

        # unborn symbolic-ref handling (any unborn symbolic ref)
        is_unborn = adapter.is_unborn_head(rp, revision)

        # effective max_commits
        effective_max = max_commits if max_commits is not None else self.max_commits

        # enumerate commits
        if is_unborn:
            # empty inventory
            scope = GitHistoryScope(
                revision=revision,
                path_filter=tuple(validated_pf),
                max_commits=effective_max,
                include_merges=include_merges,
                detect_renames=detect_renames,
                detect_copies=detect_copies,
                include_commit_messages=include_commit_messages,
            )
            return GitHistoryInventory(
                repository_namespace=repository_namespace,
                scope=scope,
                commits=(),
                changes=(),
                commit_ids=(),
                commits_by_id={},
                changes_by_commit={},
                changes_by_path={},
                commits_touching_path={},
                co_changed_paths={},
                co_change_incomplete_paths=(),
                history_is_complete=True,
                incomplete_reasons=(),
                commit_count=0,
                change_count=0,
                truncated_by_limit=False,
                shallow_boundary_commit_ids=(),
                commits_with_unavailable_baseline=(),
                detached_head=adapter.is_detached_head(rp),
                git_version=git_version,
            )

        # validate revision
        resolved = adapter.resolve_revision(rp, revision)

        # detached
        detached = adapter.is_detached_head(rp)

        # enumerate
        selection = adapter.enumerate_commits(
            rp, resolved, max_commits=effective_max, include_merges=include_merges, path_filter=tuple(validated_pf)
        )
        commit_ids = selection.commit_ids
        truncated = selection.truncated

        # shallow detection
        is_shallow = ctx.is_shallow
        shallow_boundary_ids = adapter.read_shallow_boundary_ids(ctx.git_common_dir)
        shallow_set = set(shallow_boundary_ids) & set(commit_ids)

        # fetch raw commit objects
        raw_map = adapter.fetch_commit_objects(rp, list(commit_ids))

        # build commits
        commits_list = []
        missing_metadata = False
        has_merge = False
        for cid in commit_ids:
            raw = raw_map[cid]
            if len(raw.parent_ids) > 1:
                has_merge = True
            if not raw.author_header_present or not raw.committer_header_present:
                missing_metadata = True
            # message
            if include_commit_messages:
                enc = raw.encoding or "utf-8"
                try:
                    msg = raw.message_bytes.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    raise GitParseError("decode failed")
            else:
                msg = None
            # normalize message: strip? keep as is (maybe trailing newline)
            if include_commit_messages and msg is not None:
                # remove trailing newline? spec: message is bytes after blank line normalized to str.
                # keep full
                pass
            commit = GitCommit(
                commit_id=raw.commit_id,
                parent_ids=raw.parent_ids,
                author_name=raw.author_name,
                author_email=raw.author_email,
                author_timestamp=raw.author_timestamp,
                author_timezone=raw.author_timezone,
                committer_name=raw.committer_name,
                committer_email=raw.committer_email,
                committer_timestamp=raw.committer_timestamp,
                committer_timezone=raw.committer_timezone,
                message=msg,
                is_merge=len(raw.parent_ids) > 1,
                is_root=len(raw.parent_ids) == 0,
            )
            commits_list.append(commit)

        # sort commits by committer_timestamp desc, commit_id asc
        commits_list.sort(key=lambda c: (-c.committer_timestamp, c.commit_id))
        commits_tuple = tuple(commits_list)

        # changes extraction
        changes_list = []
        unavailable_baseline_ids = []
        for commit in commits_tuple:
            raw = raw_map[commit.commit_id]
            # resolve baseline
            br = adapter.resolve_baseline(rp, commit.commit_id, raw, is_shallow)
            if br.unavailable or br.baseline_tree is None:
                unavailable_baseline_ids.append(commit.commit_id)
                # record shallow if applicable
                continue
            # diff
            commit_tree = raw.tree_id
            raw_changes = adapter.diff_trees(
                rp, br.baseline_tree, commit_tree,
                detect_renames=detect_renames, detect_copies=detect_copies,
            )
            raw_changes = sorted(raw_changes, key=lambda rc: (rc.change_type.value, rc.new_path or rc.old_path or ""))
            for rc in raw_changes:
                change_type = rc.change_type
                old_path = rc.old_path
                new_path = rc.new_path
                similarity = rc.similarity
                change_id = compute_change_id(commit.commit_id, change_type.value, old_path, new_path)
                gfc = GitFileChange(
                    change_id=change_id,
                    commit_id=commit.commit_id,
                    change_type=change_type,
                    old_path=old_path,
                    new_path=new_path,
                    similarity=similarity,
                )
                changes_list.append(gfc)
                # check change cap
                if len(changes_list) > MAX_CHANGES:
                    raise GitOutputTooLargeError("too many changes")

        changes_tuple = tuple(changes_list)

        # build indexes
        commits_by_id = build_commits_by_id(commits_tuple)
        changes_by_commit = build_changes_by_commit(commits_tuple, changes_tuple)
        changes_by_path = build_changes_by_path(changes_tuple, commits_by_id)
        commits_touching_path = build_commits_touching_path(changes_tuple)
        co_result = build_co_change_index(commits_tuple, changes_by_commit)

        # incomplete reasons
        reasons = set()
        if unavailable_baseline_ids:
            reasons.add(GitIncompleteReason.PARENT_UNAVAILABLE)
        if is_shallow and unavailable_baseline_ids:
            reasons.add(GitIncompleteReason.SHALLOW_HISTORY)
        if truncated:
            reasons.add(GitIncompleteReason.MAX_COMMITS_REACHED)
        if has_merge:
            reasons.add(GitIncompleteReason.MERGE_SEMANTICS_PARTIAL)
        if not include_merges:
            # if there were merges excluded? Hard to know; if include_merges False add reason always? Spec: MERGES_EXCLUDED when merges excluded. Add if include_merges False.
            reasons.add(GitIncompleteReason.MERGES_EXCLUDED)
        if missing_metadata:
            reasons.add(GitIncompleteReason.MISSING_METADATA)
        if validated_pf:
            reasons.add(GitIncompleteReason.PATH_FILTER_APPLIED)
        if co_result.limit_exceeded:
            reasons.add(GitIncompleteReason.CO_CHANGE_LIMIT_EXCEEDED)

        incomplete_reasons = tuple(sorted(reasons, key=lambda r: r.value))

        # shallow boundary commit ids: intersect shallow set with commit_ids
        shallow_boundary_commit_ids = tuple(sorted(shallow_set))

        # commits with unavailable baseline
        commits_with_unavailable = tuple(sorted(unavailable_baseline_ids))

        scope = GitHistoryScope(
            revision=revision,
            path_filter=tuple(validated_pf),
            max_commits=effective_max,
            include_merges=include_merges,
            detect_renames=detect_renames,
            detect_copies=detect_copies,
            include_commit_messages=include_commit_messages,
        )

        co_changed_paths = co_result.co_changed_paths

        inventory = GitHistoryInventory(
            repository_namespace=repository_namespace,
            scope=scope,
            commits=commits_tuple,
            changes=changes_tuple,
            commit_ids=tuple(c.commit_id for c in commits_tuple),
            commits_by_id=commits_by_id,
            changes_by_commit=changes_by_commit,
            changes_by_path=changes_by_path,
            commits_touching_path=commits_touching_path,
            co_changed_paths=co_changed_paths,
            co_change_incomplete_paths=co_result.incomplete_paths,
            history_is_complete=len(incomplete_reasons) == 0,
            incomplete_reasons=incomplete_reasons,
            commit_count=len(commits_tuple),
            change_count=len(changes_tuple),
            truncated_by_limit=truncated,
            shallow_boundary_commit_ids=shallow_boundary_commit_ids,
            commits_with_unavailable_baseline=commits_with_unavailable,
            detached_head=detached,
            git_version=git_version,
        )
        return inventory


def analyze_git_history(repository_path, *, repository_namespace=None, file_inventory=None,
                        revision="HEAD", max_commits=None, include_merges=True,
                        detect_renames=True, detect_copies=False, path_filter=(),
                        include_commit_messages=True, git_executable="git",
                        command_timeout_seconds=30.0, max_output_bytes=50 * 1024 * 1024):
    analyzer = GitHistoryAnalyzer(
        git_executable=git_executable,
        command_timeout_seconds=command_timeout_seconds,
        max_output_bytes=max_output_bytes,
    )
    return analyzer.analyze(
        repository_path,
        repository_namespace=repository_namespace,
        file_inventory=file_inventory,
        revision=revision,
        max_commits=max_commits,
        include_merges=include_merges,
        detect_renames=detect_renames,
        detect_copies=detect_copies,
        path_filter=path_filter,
        include_commit_messages=include_commit_messages,
    )
