"""Tests for Module 7 integration."""

import pytest

from backend.git_history import GitHistoryAnalyzer, GitIncompleteReason, GitInventoryMismatchError
from backend.code_graph.models import CodeGraphInventory, GraphNode, GraphNodeKind
from backend.code_parser.models import ParsedLanguage, ParseStatus
from backend.file_scanner.models import FileInventory
from backend.repository_loader.models import RepositoryInfo


def test_analyze_accepts_a_real_repository_info_path(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    info = RepositoryInfo(
        success=True, owner="acme", repository_name="demo",
        repo_url="https://github.com/acme/demo.git", local_path=str(repo.path),
        branch="main", current_commit=repo.head, total_files=1,
        repository_size_bytes=1, reused_existing_clone=True)
    inv = GitHistoryAnalyzer().analyze(info.local_path, revision=info.current_commit)
    assert inv.commit_count == 1


def test_file_inventory_supplies_the_namespace(git_repo):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    inventory = FileInventory(str(repo.path), 1, 1, 0, (), (), (), "acme/demo")
    inv = GitHistoryAnalyzer().analyze(repo.path, file_inventory=inventory)
    assert inv.repository_namespace == "acme/demo"


def test_mismatched_inventory_raises(git_repo, tmp_path):
    repo = git_repo()
    repo.commit({"a.txt": b"1\n"})
    inventory = FileInventory(str(tmp_path), 0, 0, 0, (), (), (), "acme/demo")
    with pytest.raises(GitInventoryMismatchError):
        GitHistoryAnalyzer().analyze(repo.path, file_inventory=inventory)


def test_file_level_association_with_module_6_nodes(git_repo):
    repo = git_repo()
    repo.commit({"src/app.py": b"x = 1\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path)
    node = GraphNode(
        node_id="code-graph:file:repo:src/app.py",
        node_kind=GraphNodeKind.FILE,
        repository_namespace="repo",
        relative_path="src/app.py",
        language=ParsedLanguage.PYTHON,
        parse_status=ParseStatus.SUCCESS,
        symbol_kind=None, name=None, qualified_name=None, location=None,
    )
    graph = CodeGraphInventory(
        repository_namespace="repo",
        nodes=(node,), edges=(), unresolved_references=(),
        outgoing_edges={}, incoming_edges={},
        nodes_by_file={"src/app.py": (node,)},
        nodes_by_qualified_name={},
    )
    changed_paths = {c.new_path for c in inv.changes if c.new_path}
    associated = sorted(changed_paths & set(graph.nodes_by_file))
    assert associated == ["src/app.py"]
    assert all(n.node_kind is GraphNodeKind.FILE
               for n in graph.nodes_by_file["src/app.py"])


def test_path_filter_selects_commits_and_keeps_the_full_change_set(git_repo):
    repo = git_repo()
    repo.commit({"keep.txt": b"1\n", "other.txt": b"1\n"})
    repo.commit({"other.txt": b"2\n"})
    inv = GitHistoryAnalyzer().analyze(repo.path, path_filter=("keep.txt",))
    assert inv.commit_count == 1
    selected = inv.commits[0]
    paths = {c.new_path for c in inv.changes_by_commit[selected.commit_id]}
    assert paths == {"keep.txt", "other.txt"}
    assert GitIncompleteReason.PATH_FILTER_APPLIED in inv.incomplete_reasons