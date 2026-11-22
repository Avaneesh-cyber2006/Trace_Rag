"""Opt-in, read-only Module 7 historical context for pinned Module 9 hits.

Git history is a *separate* evidence stream: Module 7 cannot establish that
its scope describes the source bytes in the active Module 5 generation.
These records must never be interpreted as source-revision-verified, rerank
retrieval results, or constitute proof of why a change was made.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.git_history.models import (
    GitChangeType, GitCommit, GitFileChange, GitHistoryInventory,
    GitHistoryScope, GitIncompleteReason,
)

from .errors import InvalidHybridEvidence, InvalidRetrievalQuery
from .hybrid import HybridHit

_INVALID_HISTORY = "Git history evidence is invalid or inconsistent."
_INVALID_REQUEST = "Git history retrieval request is invalid or exceeds its limit."
_MAX_HITS = 100
_MAX_HISTORY_PER_PATH = 20
_MAX_COMMITS = 10_000
_MAX_CHANGES = 100_000


@dataclass(frozen=True, slots=True)
class HistoricalChange:
    """Path-matched Git change; not source-revision-verified causal evidence."""

    commit_id: str
    change_id: str
    change_type: str
    prior_path: str | None
    committer_timestamp: int


@dataclass(frozen=True, slots=True)
class PathHistory:
    """Bounded change history for one exact path in the selected hits."""

    relative_path: str
    changes: tuple[HistoricalChange, ...] = field(repr=False)
    total_matching_changes: int = 0


@dataclass(frozen=True, slots=True)
class HistoricalContext:
    """Informational Git history alongside, never inside, verified hits."""

    paths: tuple[PathHistory, ...] = field(repr=False)
    scope_revision: str = ""
    scope_path_filter: tuple[str, ...] = ()
    history_is_complete: bool = False
    incomplete_reasons: tuple[GitIncompleteReason, ...] = ()
    source_revision_verified: bool = False


class HistoryAnnotator:
    """Build bounded, deterministic, exact-current-path summaries.

    We intentionally use only the inventory's primary commit/change tuples.
    Its secondary path indexes might be incomplete, and a former rename path
    is not evidence that the indexed current path existed at that revision.
    No Git commands, disk reads, network calls, or provider calls occur here.
    """

    __slots__ = ("_namespace", "_inventory", "_per_path", "_changes_by_path")

    def __init__(
        self, repository_namespace: str, inventory: GitHistoryInventory,
        per_path: int = 5,
    ) -> None:
        if (
            type(repository_namespace) is not str or not repository_namespace
            or type(inventory) is not GitHistoryInventory
            or type(per_path) is not int
            or not 1 <= per_path <= _MAX_HISTORY_PER_PATH
        ):
            raise InvalidRetrievalQuery(_INVALID_REQUEST)
        if inventory.repository_namespace != repository_namespace:
            raise InvalidHybridEvidence(_INVALID_HISTORY)
        if (
            type(inventory.commits) is not tuple
            or type(inventory.changes) is not tuple
            or len(inventory.commits) > _MAX_COMMITS
            or len(inventory.changes) > _MAX_CHANGES
            or type(inventory.scope) is not GitHistoryScope
            or type(inventory.scope.revision) is not str
            or type(inventory.scope.path_filter) is not tuple
            or type(inventory.history_is_complete) is not bool
            or type(inventory.incomplete_reasons) is not tuple
            or any(type(reason) is not GitIncompleteReason
                   for reason in inventory.incomplete_reasons)
        ):
            raise InvalidHybridEvidence(_INVALID_HISTORY)

        # Validate primary identities, references, and fields consumed here
        # before acquiring the pinned Chroma lease or embedding the query.
        # Module 7 dataclasses may be mutated after construction.
        commits: dict[str, GitCommit] = {}
        for commit in inventory.commits:
            if (
                type(commit) is not GitCommit
                or type(commit.commit_id) is not str or not commit.commit_id
                or type(commit.committer_timestamp) is not int
                or commit.commit_id in commits
            ):
                raise InvalidHybridEvidence(_INVALID_HISTORY)
            commits[commit.commit_id] = commit

        changes_by_path: dict[str, list[tuple[GitCommit, GitFileChange]]] = {}
        change_ids: set[str] = set()
        for change in inventory.changes:
            if (
                type(change) is not GitFileChange
                or type(change.change_id) is not str or not change.change_id
                or change.change_id in change_ids
                or type(change.commit_id) is not str
                or change.commit_id not in commits
                or type(change.change_type) is not GitChangeType
                or (change.old_path is not None and type(change.old_path) is not str)
                or (change.new_path is not None and type(change.new_path) is not str)
            ):
                raise InvalidHybridEvidence(_INVALID_HISTORY)
            change_ids.add(change.change_id)
            if change.new_path is not None:
                changes_by_path.setdefault(change.new_path, []).append(
                    (commits[change.commit_id], change)
                )

        self._namespace = repository_namespace
        self._inventory = inventory
        self._per_path = per_path
        self._changes_by_path = {
            path: tuple(sorted(matches, key=lambda pair: (
                -pair[0].committer_timestamp, pair[0].commit_id, pair[1].change_id,
            )))
            for path, matches in changes_by_path.items()
        }

    def annotate(self, hits: tuple[HybridHit, ...]) -> HistoricalContext:
        if type(hits) is not tuple or len(hits) > _MAX_HITS:
            raise InvalidRetrievalQuery(_INVALID_REQUEST)
        paths: list[str] = []
        seen: set[str] = set()
        for rank, hit in enumerate(hits, 1):
            if (
                type(hit) is not HybridHit
                or hit.repository_namespace != self._namespace
                or type(hit.rank) is not int or hit.rank != rank
                or type(hit.relative_path) is not str or not hit.relative_path
            ):
                raise InvalidHybridEvidence(_INVALID_HISTORY)
            if hit.relative_path not in seen:
                paths.append(hit.relative_path)
                seen.add(hit.relative_path)

        output: list[PathHistory] = []
        for path in paths:
            matches = self._changes_by_path.get(path, ())
            output.append(PathHistory(
                relative_path=path,
                changes=tuple(
                    HistoricalChange(
                        commit_id=change.commit_id,
                        change_id=change.change_id,
                        change_type=change.change_type.value,
                        prior_path=change.old_path,
                        committer_timestamp=commit.committer_timestamp,
                    ) for commit, change in matches[:self._per_path]
                ),
                total_matching_changes=len(matches),
            ))
        return HistoricalContext(
            paths=tuple(output),
            scope_revision=self._inventory.scope.revision,
            scope_path_filter=self._inventory.scope.path_filter,
            history_is_complete=self._inventory.history_is_complete,
            incomplete_reasons=self._inventory.incomplete_reasons,
            # Module 7 has no source-bytes/Git-blob binding to Module 5.
            source_revision_verified=False,
        )
