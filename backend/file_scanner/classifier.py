"""Pure language and category classification rules."""

from pathlib import PurePosixPath

from .filters import CONFIG_EXTENSIONS, SOURCE_EXTENSIONS
from .models import FileCategory

LANGUAGES = {
    ".py": "python", ".java": "java", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".c": "c", ".h": "c/cpp",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hh": "cpp", ".hpp": "cpp",
    ".hxx": "cpp", ".cs": "csharp", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift",
    ".scala": "scala", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".ps1": "powershell", ".sql": "sql", ".prisma": "prisma",
}
SPECIAL_LANGUAGES = {
    "dockerfile": "dockerfile", "makefile": "make", "gemfile": "ruby", "rakefile": "ruby",
}
BUILD_FILENAMES = frozenset({
    "package.json", "requirements.txt", "pyproject.toml", "pipfile", "pom.xml",
    "build.gradle", "build.gradle.kts", "dockerfile", "makefile", "cargo.toml",
    "go.mod", "composer.json", "gemfile", "rakefile", "procfile", "jenkinsfile",
})
CONFIG_FILENAMES = frozenset({".gitignore", ".gitattributes", ".editorconfig", ".dockerignore"})
DOCUMENT_STEMS = ("readme", "license", "notice", "contributing", "changelog", "code_of_conduct")
TEST_COMPONENTS = frozenset({"test", "tests", "testing", "__tests__", "spec"})


def detect_language(filename: str, extension: str) -> str | None:
    return SPECIAL_LANGUAGES.get(filename.casefold()) or LANGUAGES.get(extension.casefold())


def _is_test_file(parts: tuple[str, ...], filename: str, extension: str) -> bool:
    if any(part in TEST_COMPONENTS for part in parts[:-1]):
        return True
    if extension not in SOURCE_EXTENSIONS:
        return False
    stem = filename[: -len(extension)] if extension else filename
    return (
        stem.startswith("test_") or stem.endswith("_test") or stem.endswith("test")
        or ".test." in filename or ".spec." in filename
    )


def classify_file(relative_path: str, filename: str, extension: str) -> FileCategory:
    parts = tuple(part.casefold() for part in PurePosixPath(relative_path).parts)
    name = filename.casefold()
    suffix = extension.casefold()
    if _is_test_file(parts, name, suffix):
        return FileCategory.TEST
    if suffix in {".sql", ".prisma"} or any(part in {"migration", "migrations"} for part in parts[:-1]):
        return FileCategory.DATABASE
    if name in BUILD_FILENAMES:
        return FileCategory.BUILD
    if name in CONFIG_FILENAMES or name.endswith((".env.example", ".env.sample", ".env.template")) or suffix in CONFIG_EXTENSIONS:
        return FileCategory.CONFIG
    if suffix in {".md", ".rst"} or name.startswith(DOCUMENT_STEMS):
        return FileCategory.DOCUMENTATION
    if suffix in SOURCE_EXTENSIONS:
        return FileCategory.SOURCE
    return FileCategory.OTHER_TEXT
