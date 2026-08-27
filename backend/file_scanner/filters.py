"""Deterministic allowlist and content filters."""

import codecs
import os
import stat
import unicodedata

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


_ALLOWED_CONTROLS = {"\t", "\n", "\r", "\f", "\b"}
_BOMS = (
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


def _decoded_text_is_binary(text: str) -> bool:
    if not text:
        return False
    controls = sum(
        character not in _ALLOWED_CONTROLS
        and unicodedata.category(character).startswith("C")
        and unicodedata.category(character) != "Cf"
        for character in text
    )
    return controls / len(text) > 0.30


def is_binary_sample(sample: bytes) -> bool:
    if not sample:
        return False
    for bom, encoding in _BOMS:
        if sample.startswith(bom):
            try:
                return _decoded_text_is_binary(sample.decode(encoding, errors="strict"))
            except UnicodeDecodeError:
                return True
    if b"\x00" in sample:
        return True
    try:
        return _decoded_text_is_binary(sample.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        controls = sum(
            (byte < 0x20 and byte not in {0x08, 0x09, 0x0A, 0x0C, 0x0D}) or byte == 0x7F
            for byte in sample
        )
        return controls / len(sample) > 0.30


def is_link_or_reparse(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    metadata = entry.stat(follow_symlinks=False)
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)
