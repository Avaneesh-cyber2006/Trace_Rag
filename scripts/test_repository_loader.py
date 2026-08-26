"""Manual command-line check for TraceRAG's Repository Loader."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Protocol, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.repository_loader import (  # noqa: E402
    RepositoryInfo,
    RepositoryLoader,
    RepositoryLoaderError,
)


class Loader(Protocol):
    def load(self, repository_url: str) -> RepositoryInfo:
        """Load and describe one repository."""


def _format_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1000
    raise AssertionError("unreachable")


def main(argv: Sequence[str] | None = None, loader: Loader | None = None) -> int:
    """Run the manual loader check and return a process exit code."""
    parser = argparse.ArgumentParser(description="Load a public GitHub repository.")
    parser.add_argument("repository_url", help="Public GitHub repository root URL")
    arguments = parser.parse_args(argv)
    active_loader = loader or RepositoryLoader()

    print("TraceRAG Repository Loader")
    print("==========================")
    print()
    try:
        info = active_loader.load(arguments.repository_url)
    except RepositoryLoaderError as exc:
        print("Status: ERROR")
        print(f"Reason: {exc}")
        return 1

    commit = f"{info.current_commit[:12]}..." if info.current_commit else "Unknown"
    print("Status:              SUCCESS")
    print(f"Repository:          {info.owner}/{info.repository_name}")
    print(f"URL:                 {info.repo_url}")
    print(f"Branch:              {info.branch or 'Detached HEAD'}")
    print(f"Commit:              {commit}")
    print(f"Files:               {info.total_files}")
    print(f"Size:                {_format_size(info.repository_size_bytes)}")
    print(f"Local Path:          {info.local_path}")
    print(f"Existing Clone:      {'Yes' if info.reused_existing_clone else 'No'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
