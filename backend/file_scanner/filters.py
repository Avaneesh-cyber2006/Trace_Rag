"""Deterministic allowlist and content filters."""

from .models import IgnoreReason

DEFAULT_IGNORED_DIRECTORIES = frozenset({
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "target", "coverage",
    ".next", ".nuxt", ".cache", ".gradle", ".idea", ".vscode", "vendor",
    "out", "bin", "obj",
})

SOURCE_EXTENSIONS = frozenset({
    ".py", ".java", ".js", ".jsx", ".ts", ".tsx", ".c", ".cc", ".cpp",
    ".cxx", ".h", ".hh", ".hpp", ".hxx", ".cs", ".go", ".rs", ".rb",
    ".php", ".kt", ".kts", ".swift", ".scala", ".sh", ".bash", ".zsh", ".ps1",
})
CONFIG_EXTENSIONS = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".properties", ".xml", ".sql", ".prisma",
})
DOCUMENT_EXTENSIONS = frozenset({".md", ".rst", ".txt"})
SUPPORTED_EXTENSIONS = SOURCE_EXTENSIONS | CONFIG_EXTENSIONS | DOCUMENT_EXTENSIONS

SPECIAL_FILENAMES = frozenset({
    "dockerfile", "makefile", "gemfile", "rakefile", "pipfile", "procfile",
    "jenkinsfile", "license", "notice", ".gitignore", ".gitattributes",
    ".editorconfig", ".dockerignore", "package.json", "requirements.txt",
    "pyproject.toml", "pom.xml", "build.gradle", "build.gradle.kts",
    "cargo.toml", "go.mod", "composer.json",
})
LOCKFILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "pipfile.lock",
    "poetry.lock", "composer.lock", "cargo.lock", "gemfile.lock",
})
SENSITIVE_NAMES = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "credentials.json",
    "service-account.json",
})
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
SAFE_ENV_SUFFIXES = (".env.example", ".env.sample", ".env.template")


def _is_safe_environment_template(name: str) -> bool:
    return name.endswith(SAFE_ENV_SUFFIXES)


def _is_sensitive_filename(name: str) -> bool:
    if name in SENSITIVE_NAMES or name.endswith(SENSITIVE_SUFFIXES):
        return True
    return (name == ".env" or name.endswith(".env") or ".env." in name) and not _is_safe_environment_template(name)


def is_supported_filename(filename: str) -> bool:
    name = filename.casefold()
    if _is_safe_environment_template(name) or name in SPECIAL_FILENAMES:
        return True
    suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    return suffix in SUPPORTED_EXTENSIONS


def classify_filename(filename: str) -> IgnoreReason | None:
    name = filename.casefold()
    if _is_sensitive_filename(name):
        return IgnoreReason.SENSITIVE_FILE
    if name in LOCKFILES:
        return IgnoreReason.LOCKFILE
    if name.endswith((".min.js", ".min.css")):
        return IgnoreReason.MINIFIED
    if not is_supported_filename(filename):
        return IgnoreReason.UNSUPPORTED_TYPE
    return None
