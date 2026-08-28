"""Closed, lazily initialized Tree-sitter parser registry."""

from collections.abc import Callable
from dataclasses import dataclass

from tree_sitter import Language, Parser

from backend.code_parser.exceptions import ParserConfigurationError
from backend.code_parser.models import ParsedLanguage
from backend.file_scanner.models import ScannedFile


class ParserUnavailable(Exception):
    """Raised internally when one configured grammar cannot initialize."""


@dataclass(frozen=True, slots=True)
class ParserSpec:
    inventory_language: str
    language: ParsedLanguage
    extension: str
    extractor_key: str
    language_factory: Callable[[], Language]


@dataclass(frozen=True, slots=True)
class ParserHandle:
    spec: ParserSpec
    parser: Parser


def _python_language() -> Language:
    import tree_sitter_python

    return Language(tree_sitter_python.language())


def _java_language() -> Language:
    import tree_sitter_java

    return Language(tree_sitter_java.language())


def _javascript_language() -> Language:
    import tree_sitter_javascript

    return Language(tree_sitter_javascript.language())


def _typescript_language() -> Language:
    import tree_sitter_typescript

    return Language(tree_sitter_typescript.language_typescript())


def _tsx_language() -> Language:
    import tree_sitter_typescript

    return Language(tree_sitter_typescript.language_tsx())


class ParserRegistry:
    """Maps trusted scanner metadata to a closed set of parser factories."""

    def __init__(self, *, specs: tuple[ParserSpec, ...] | None = None) -> None:
        configured_specs = self._default_specs() if specs is None else specs
        self._validate_specs(configured_specs)
        self._specs_by_selector = {
            (spec.inventory_language, spec.extension): spec for spec in configured_specs
        }
        self._specs_by_language = {spec.language: spec for spec in configured_specs}
        self._languages: dict[ParsedLanguage, Language] = {}
        self._parsers: dict[ParsedLanguage, ParserHandle] = {}
        self._unavailable_languages: set[ParsedLanguage] = set()

    def select(self, file: ScannedFile) -> ParserSpec | None:
        spec = self._specs_by_selector.get((file.language, file.extension))
        if spec is not None:
            return spec
        if file.language == "javascript" and file.extension == ".jsx":
            return self._specs_by_selector.get(("javascript", ".js"))
        return None

    def get_parser(self, spec: ParserSpec) -> ParserHandle:
        if self._specs_by_language.get(spec.language) is not spec:
            raise ParserConfigurationError("Invalid parser registry configuration.")

        cached = self._parsers.get(spec.language)
        if cached is not None:
            return cached
        if spec.language in self._unavailable_languages:
            raise ParserUnavailable("Parser initialization is unavailable.")

        try:
            language = spec.language_factory()
            parser = Parser(language)
        except Exception:
            self._unavailable_languages.add(spec.language)
            raise ParserUnavailable("Parser initialization is unavailable.") from None

        self._languages[spec.language] = language
        handle = ParserHandle(spec=spec, parser=parser)
        self._parsers[spec.language] = handle
        return handle

    @staticmethod
    def _default_specs() -> tuple[ParserSpec, ...]:
        return (
            ParserSpec("python", ParsedLanguage.PYTHON, ".py", "python", _python_language),
            ParserSpec("java", ParsedLanguage.JAVA, ".java", "java", _java_language),
            ParserSpec(
                "javascript",
                ParsedLanguage.JAVASCRIPT,
                ".js",
                "javascript",
                _javascript_language,
            ),
            ParserSpec(
                "typescript",
                ParsedLanguage.TYPESCRIPT,
                ".ts",
                "typescript",
                _typescript_language,
            ),
            ParserSpec("typescript", ParsedLanguage.TSX, ".tsx", "tsx", _tsx_language),
        )

    @staticmethod
    def _validate_specs(specs: tuple[ParserSpec, ...]) -> None:
        selectors: set[tuple[str, str]] = set()
        languages: set[ParsedLanguage] = set()
        for spec in specs:
            selector = (spec.inventory_language, spec.extension)
            if (
                not spec.extension
                or selector in selectors
                or spec.language in languages
                or spec.extractor_key not in (
                    "python",
                    "java",
                    "javascript",
                    "typescript",
                    "tsx",
                )
            ):
                raise ParserConfigurationError("Invalid parser registry configuration.")
            selectors.add(selector)
            languages.add(spec.language)
