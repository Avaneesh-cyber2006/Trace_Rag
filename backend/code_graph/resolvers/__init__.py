"""Static dispatch to language-specific graph resolvers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.code_parser.models import ParsedLanguage

from .base import ImportEnvironment, ImportResult, Resolution
from backend.code_parser.models import ImportInfo, ParsedFile

if TYPE_CHECKING:
    from ..indexes import GraphIndexes
    from ..references import Reference


def load_resolvers():
    """Import all fixed resolver modules without dynamic import machinery."""

    from . import ecmascript, java, python

    return {
        ParsedLanguage.PYTHON: python,
        ParsedLanguage.JAVA: java,
        ParsedLanguage.JAVASCRIPT: ecmascript,
        ParsedLanguage.TYPESCRIPT: ecmascript,
        ParsedLanguage.TSX: ecmascript,
    }


def resolve_import(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> ImportResult:
    """Resolve one import using the resolver selected by its parsed language."""

    return load_resolvers()[file.language].resolve_import(index, file, item)


def resolve_reference(
    index: GraphIndexes, environment: ImportEnvironment, reference: Reference
) -> Resolution:
    """Resolve one call or type reference using its parsed language."""

    return load_resolvers()[reference.language].resolve_reference(
        index, environment, reference
    )
