"""Shared in-memory fixtures for code graph tests."""

from pathlib import PurePosixPath
from types import SimpleNamespace

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
