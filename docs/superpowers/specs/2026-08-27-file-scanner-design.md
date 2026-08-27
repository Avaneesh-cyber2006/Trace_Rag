# TraceRAG Module 2: File Scanner & Filter Design

## 1. Scope

Module 2 accepts an existing local repository directory, safely inventories its useful files, classifies the included text files, and explains exclusions for filesystem entries it actually examines. Its normal input is `RepositoryInfo.local_path` from Module 1, but it deliberately accepts `Path | str` so it can be tested and used independently.

The module treats every repository entry as untrusted data. It inspects paths, metadata, and at most a small byte prefix. It never executes, imports, evaluates, deserializes, installs, builds, tests, or otherwise activates repository-controlled content.

The design priorities, in order, are security, correctness, determinism, simple interfaces, testability, portability, extensibility, and performance. V1 uses only the Python standard library and supports Python 3.11+ on Windows 10/11 and Linux.

## 2. Relationship to Existing Module 1

The current Repository Loader exposes frozen, slotted dataclasses, a primary class plus convenience function, a focused exception hierarchy, and explicit package exports. Module 2 follows those conventions without modifying or importing Module 1 internals.

Normal integration remains:

```python
from backend.file_scanner import FileScanner
from backend.repository_loader import RepositoryLoader

repository = RepositoryLoader().load("https://github.com/example/project")
inventory = FileScanner().scan(repository.local_path)
```

Module 2 does not accept or depend on the complete `RepositoryInfo` object. It does not clone, call GitHub, inspect Git history, or require a Git repository marker. Any accessible local directory is a valid potential scan root.

## 3. Considered Approaches

### Recommended: conservative allowlist with layered filters

Known useful extensions and special filenames are eligible. Cheap name/type checks run before metadata and prefix reads. Unknown types are excluded even if they might be textual. This is predictable, secure, easy to test, and produces a bounded contract for Module 3.

### Alternative: accept any file that passes binary detection

This captures uncommon languages automatically, but it also admits logs, datasets, generated artifacts, credential-like files with unfamiliar names, and formats Module 3 cannot interpret. It makes the inventory less intentional and is rejected for V1.

### Alternative: configurable rule-engine object

A first-class rules engine could expose every ignore set, language map, and precedence rule. That is flexible but premature. V1 keeps immutable module defaults and exposes only size and sample-size configuration. Focused constants and pure helper functions leave a straightforward extension path without making the public API unstable now.

## 4. Public API

```python
class FileScanner:
    def __init__(
        self,
        max_file_size_bytes: int = 1_000_000,
        binary_sample_size: int = 8192,
    ) -> None: ...

    def scan(self, repository_path: Path | str) -> FileInventory: ...


def scan_repository(
    repository_path: Path | str,
    max_file_size_bytes: int = 1_000_000,
    binary_sample_size: int = 8192,
) -> FileInventory: ...
```

The constructor mirrors Module 1's simple configuration style. Both values must be positive integers; booleans are rejected even though `bool` subclasses `int`. Invalid values raise `ScannerConfigurationError` rather than `ValueError`, giving Module 2 a consistent public error boundary.

The public `backend.file_scanner` package exports `FileScanner`, `scan_repository`, all public models/enums, and the exception hierarchy. Internal rule constants and helpers are not public API.

## 5. Data Models

All models are `@dataclass(frozen=True, slots=True)`. Enums inherit from `str, Enum` so their values serialize naturally while remaining stable typed contracts.

```python
class FileCategory(str, Enum):
    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    DATABASE = "database"
    DOCUMENTATION = "documentation"
    BUILD = "build"
    OTHER_TEXT = "other_text"


class IgnoreReason(str, Enum):
    SENSITIVE_FILE = "sensitive_file"
    LOCKFILE = "lockfile"
    UNSUPPORTED_TYPE = "unsupported_type"
    MINIFIED = "minified"
    TOO_LARGE = "too_large"
    BINARY = "binary"
    UNREADABLE = "unreadable"
    SYMLINK = "symlink"


class SkippedDirectoryReason(str, Enum):
    IGNORED_DIRECTORY = "ignored_directory"
    UNREADABLE = "unreadable"
    SYMLINK = "symlink"


@dataclass(frozen=True, slots=True)
class ScannedFile:
    relative_path: str
    filename: str
    extension: str
    language: str | None
    category: FileCategory
    size_bytes: int


@dataclass(frozen=True, slots=True)
class IgnoredFile:
    relative_path: str
    reason: IgnoreReason


@dataclass(frozen=True, slots=True)
class SkippedDirectory:
    relative_path: str
    reason: SkippedDirectoryReason


@dataclass(frozen=True, slots=True)
class FileInventory:
    repository_path: str
    total_files_seen: int
    included_files: int
    ignored_files: int
    files: tuple[ScannedFile, ...]
    ignored: tuple[IgnoredFile, ...]
    skipped_directories: tuple[SkippedDirectory, ...]
```

`ScannedFile` intentionally does not expose an absolute path. `FileInventory.repository_path` is the one resolved absolute scan root; consumers construct `Path(inventory.repository_path) / scanned_file.relative_path`. This avoids duplicating machine-specific paths, keeps file identity stable across machines, and makes serialized citations repository-relative. A later consumer must repeat containment and symlink checks when opening a file because filesystem state can change after scanning.

`extension` is the final suffix in lowercase including the leading dot, or `""` for extensionless special files. Compound names such as `.env.example` are recognized by filename rules rather than represented as compound extensions.

`SkippedDirectory` makes pruning and partial traversal visible without manufacturing one ignored-file record per unseen descendant. It also distinguishes a deliberately pruned directory from a directory that could not be enumerated.

## 6. Proposed Components and Package Structure

```text
backend/file_scanner/
├── __init__.py       # explicit public exports
├── scanner.py        # validation, traversal, orchestration, counters
├── filters.py        # immutable rules and file eligibility/binary decisions
├── classifier.py     # language and category classification
├── models.py         # enums and frozen result values
└── exceptions.py     # public exception hierarchy

tests/
└── test_file_scanner.py
```

This matches the existing package organization and the proposed layout. A separate traversal or binary module is not justified for V1. `filters.py` owns pure, independently testable decisions; `scanner.py` owns filesystem effects; `classifier.py` owns deterministic metadata mapping.

## 7. Scan Flow

For each scan:

1. Validate and strictly resolve the supplied root.
2. Start an iterative `os.scandir` traversal at that root.
3. For each directory entry, inspect without following symlinks.
4. Record every symlink and never follow it.
5. Prune ignored directory components before descent.
6. For each regular file, apply filename/type rules.
7. Read file size with `stat(follow_symlinks=False)` and reject oversized files before opening them.
8. Read at most `binary_sample_size` bytes and apply the binary heuristic.
9. Determine language and category using path/name/extension rules.
10. Collect included files, ignored files, and skipped directories.
11. Sort all three collections by normalized relative path.
12. Construct `FileInventory` and log aggregate completion counts.

No complete file is read. No hash, AST, configuration parse, MIME library, subprocess, Git command, or network request is involved.

## 8. Repository Path Validation and Containment

`scan()` converts the input with `Path(repository_path)` and calls `resolve(strict=True)`. Resolution failures, a nonexistent path, and runtime resolution loops become `InvalidRepositoryPath`; a resolved non-directory is also invalid. The public exception is raised without exposing a raw `OSError`.

The resolved directory is the immutable logical root for the scan. Traversal begins only from directory entries obtained beneath that root. V1 never follows symlinks, so an entry cannot redirect traversal elsewhere. Relative paths are derived from known root-relative traversal components, not from unchecked repository strings. As a defensive invariant, any path resolved for an operation must be contained by the root using `Path.is_relative_to(root)`; containment is component-based, never string-prefix-based.

The scanner does not require `.git` to exist. Failure to open the root itself is fatal. A descendant that disappears or becomes inaccessible is recoverable and reported as described below.

## 9. Directory Pruning

Directory names are matched as complete components using `casefold()` so behavior is deterministic across case-sensitive and case-insensitive hosts. Original spelling is preserved in returned paths.

The initial immutable ignored-directory set is:

```text
.git, node_modules, venv, .venv, __pycache__, .pytest_cache,
.mypy_cache, .ruff_cache, dist, build, target, coverage, .next,
.nuxt, .cache, .gradle, .idea, .vscode, vendor, out, bin, obj
```

Matching a component prevents false positives such as `builder`, `distribution`, and `targeting`. `.github` is intentionally not ignored; CI workflows are useful configuration. Hidden directories are not ignored merely for being hidden.

Each pruned directory produces one `SkippedDirectory(..., IGNORED_DIRECTORY)` entry. Descendants are not enumerated, do not appear in `ignored`, and do not affect file counters. This bounds time and memory for dependency trees such as `node_modules`.

The default set remains an internal immutable `frozenset`. Future configuration can add a frozen config object if real use cases require customization; V1 does not expose dozens of constructor arguments.

## 10. Supported Files and File Filtering

V1 uses an intentional allowlist. Candidate source extensions include:

```text
.py, .java, .js, .jsx, .ts, .tsx, .c, .cc, .cpp, .cxx, .h, .hh,
.hpp, .hxx, .cs, .go, .rs, .rb, .php, .kt, .kts, .swift, .scala,
.sh, .bash, .zsh, .ps1
```

Candidate config/data/build extensions include:

```text
.json, .yaml, .yml, .toml, .ini, .cfg, .conf, .properties, .xml,
.sql, .prisma
```

Candidate documentation/text extensions include:

```text
.md, .rst, .txt
```

Special filenames add useful extensionless or dotfiles, including `Dockerfile`, `Makefile`, `Gemfile`, `Rakefile`, `Pipfile`, `Procfile`, `Jenkinsfile`, `LICENSE`, `NOTICE`, `.gitignore`, `.gitattributes`, `.editorconfig`, `.dockerignore`, and safe environment templates. Build manifests such as `package.json`, `requirements.txt`, `pyproject.toml`, `pom.xml`, `build.gradle`, `build.gradle.kts`, `Cargo.toml`, `go.mod`, and `composer.json` are explicitly eligible.

Unknown extensions and unknown extensionless files are `UNSUPPORTED_TYPE`, even when their prefix appears textual. This is a deliberate conservative boundary; adding a language later is an explicit rule change with tests.

Known compiled files, images, audio/video, archives, fonts, database binaries, and other non-source formats are rejected as `UNSUPPORTED_TYPE`. At minimum this covers all examples in the request. Normal `.svg` and `.svgz` are also `UNSUPPORTED_TYPE`: SVG may be XML text, but its drawing payload is not useful input for source analysis and can be very large/generated.

Lockfiles are matched by whole case-folded filename and return `LOCKFILE`: `package-lock.json`, `yarn.lock`, `pnpm-lock.yaml`, `Pipfile.lock`, `poetry.lock`, `composer.lock`, `Cargo.lock`, and `Gemfile.lock`. Their corresponding manifests remain supported.

Filename suffixes `.min.js` and `.min.css`, case-insensitively, return `MINIFIED`. `.css` is otherwise unsupported in V1; the explicit minified rule still gives the more informative reason. V1 adds no content-based minification or general generated-file heuristic.

## 11. Sensitive-File Policy

Sensitive-name checks happen before metadata reads or file opens. Actual environment files are excluded without reading content. Safe templates are the only `.env` family members admitted.

Allowed, case-insensitive safe patterns are exactly:

```text
.env.example
.env.sample
.env.template
*.env.example
*.env.sample
*.env.template
```

Names equal to `.env`, ending in `.env`, or containing `.env.` return `SENSITIVE_FILE` unless the complete case-folded filename ends in one of the three explicit safe suffixes above. This excludes `.env.local`, `.env.production`, `.env.development`, `.env.test`, `app.env`, and `app.env.production`, while allowing `app.env.example`.

Obvious credential/private-key names and suffixes also return `SENSITIVE_FILE`, including `.pem`, `.key`, `.p12`, `.pfx`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `credentials.json`, and `service-account.json`. Matching is case-insensitive and filename-based. V1 does not inspect content, search for tokens, or claim to be a complete secret scanner.

## 12. Size Filtering

The default maximum is 1,000,000 bytes. This is small enough to protect downstream parsing/chunking while accommodating normal source, documentation, and manifest files. It is configurable because generated code and large schemas vary by project.

The scanner obtains `st_size` before opening the file. A file with `size_bytes > max_file_size_bytes` returns `TOO_LARGE`; a file exactly equal to the limit remains eligible. Oversized files are never sampled. Zero-byte supported files are included and treated as text.

## 13. Binary Detection

Only candidates that pass name/type and size filters are sampled. The scanner opens in binary mode and performs one `read(binary_sample_size)`. Classification is deterministic:

1. An empty sample is text.
2. A recognized UTF-8, UTF-16, or UTF-32 BOM is decoded strictly with its indicated codec. Successful decoding proceeds to the decoded control-character check; failure is binary.
3. Without such a BOM, any NUL byte is binary.
4. Otherwise, strict UTF-8 decoding is attempted. On success, the decoded control-character rule applies.
5. If UTF-8 decoding fails, use a byte-level fallback for possible legacy single-byte text. Bytes below `0x20` other than tab, line feed, carriage return, form feed, and backspace, plus `0x7f`, are controls. If controls exceed 30% of the sample, the file is binary; otherwise it is accepted as likely legacy text.
6. For decoded text, Unicode characters whose category starts with `C` are controls, except tab, line feed, carriage return, form feed, backspace, and ordinary format characters required by Unicode text. If controls exceed 30%, the file is binary.

The comparison is strictly `control_count / sample_length > 0.30`; exactly 30% is text. High-bit bytes alone are not binary, so ordinary non-UTF-8 text is not automatically rejected. The heuristic is intentionally explainable rather than perfect. Module 2 does not promise an encoding; Module 3 must make its own decoding decision when parsing a complete file.

Read/open errors return `UNREADABLE`. The scanner never logs sampled bytes.

## 14. Language Detection

Language detection is a pure, deterministic map from lowercase extension or case-folded special filename. It does not inspect file contents or parse syntax.

Representative mappings are:

```text
.py -> python           .java -> java
.js/.jsx -> javascript .ts/.tsx -> typescript
.c -> c                 .h -> c/cpp
.cc/.cpp/.cxx -> cpp    .hh/.hpp/.hxx -> cpp
.cs -> csharp           .go -> go
.rs -> rust             .rb -> ruby
.php -> php             .kt/.kts -> kotlin
.swift -> swift         .scala -> scala
.sh/.bash/.zsh -> shell .ps1 -> powershell
.sql -> sql             .prisma -> prisma
Dockerfile -> dockerfile
Makefile -> make
Gemfile/Rakefile -> ruby
```

Config, documentation, and generic text formats have `language=None`, except formats with meaningful downstream parsers such as SQL and Prisma. The neutral `c/cpp` label for `.h` avoids pretending headers are always one language.

## 15. File Classification and Precedence

Classification evaluates included candidates in this fixed order:

```text
TEST -> DATABASE -> BUILD -> CONFIG -> DOCUMENTATION -> SOURCE -> OTHER_TEXT
```

- `TEST`: a complete path component is `test`, `tests`, `testing`, `__tests__`, or `spec`; or a source-like filename matches conventions such as `test_*.py`, `*_test.py`, `*Test.java`, `*.test.<source-extension>`, or `*.spec.<source-extension>`. A file within a recognized test directory wins over its format, so `tests/config.json` is `TEST`.
- `DATABASE`: `.sql` or `.prisma`, or a supported text file beneath a `migration`/`migrations` component. This wins over general build/config rules but not an explicit test context.
- `BUILD`: known dependency/build manifests and build-tool filenames, including those listed in Section 10.
- `CONFIG`: supported structured configuration extensions, safe environment templates, recognized configuration dotfiles, and workflow/config files such as `.github/workflows/*.yml`.
- `DOCUMENTATION`: `.md`, `.rst`, and recognized documentation names such as README, LICENSE, NOTICE, CONTRIBUTING, CHANGELOG, and CODE_OF_CONDUCT with supported optional suffixes.
- `SOURCE`: a supported programming-language extension not matched earlier.
- `OTHER_TEXT`: an eligible known text type such as a generic `.txt` or an explicitly supported special text file that matches no stronger category.

All component, filename, and extension comparisons are case-insensitive through `casefold()` for portable outcomes. Returned paths and filenames retain original casing. Thus `README.md`, `readme.MD`, `Dockerfile`, and `dockerfile` classify consistently.

## 16. Ignore-Reason Precedence

A file can match several rules. The first applicable rule wins:

```text
SYMLINK
SENSITIVE_FILE
LOCKFILE
MINIFIED
UNSUPPORTED_TYPE
TOO_LARGE
UNREADABLE
BINARY
```

Symlink and sensitive checks are security-first and require no content access. Lockfile and minified reasons are more informative than their underlying supported/unsupported extension. Unsupported types are rejected before `stat`/read work. Size is checked before opening content. `UNREADABLE` is selected at the exact filesystem operation that fails; it necessarily prevents any later decision. `BINARY` is last because it requires a prefix read.

Examples:

- `.env.production` is `SENSITIVE_FILE` even if oversized.
- `package-lock.json` is `LOCKFILE` even if oversized.
- `huge.min.js` is `MINIFIED` without a metadata read.
- `huge.png` is `UNSUPPORTED_TYPE`, not `TOO_LARGE`.
- A supported oversized `data.json` is `TOO_LARGE` and is never sampled.

## 17. Symlink and Path Safety

V1 ignores all symbolic links, whether they appear to target files or directories and whether their targets are inside or outside the repository. They are inspected with `follow_symlinks=False`, never resolved for inclusion, and never traversed or opened.

A file symlink produces `IgnoredFile(..., SYMLINK)` and counts as a seen/ignored file-like entry. A directory symlink produces `SkippedDirectory(..., SYMLINK)` and does not affect file counters. Broken links follow the same rule. This simple policy removes escape and cycle risks and behaves consistently across platforms.

Windows junctions/reparse points must also not be followed. Before directory descent, the scanner uses non-following metadata and checks `stat.FILE_ATTRIBUTE_REPARSE_POINT` when `st_file_attributes` is present. A reparse-point directory is recorded as `SkippedDirectory(..., SYMLINK)`; a file-like reparse point is `IgnoredFile(..., SYMLINK)`. Platform-specific creation tests are skipped only where the host does not permit creating the relevant link type.

## 18. Error Handling

```text
FileScannerError
├── InvalidRepositoryPath
├── RepositoryScanError
└── ScannerConfigurationError
```

No separate public `FileInspectionError` is needed because individual inspection failures are recoverable inventory outcomes rather than raised errors.

- `ScannerConfigurationError`: invalid constructor values; fatal before scanning.
- `InvalidRepositoryPath`: missing, unresolvable, non-directory, or initially inaccessible root; fatal.
- `RepositoryScanError`: traversal cannot begin or a root-level invariant fails after validation; fatal.
- `IgnoredFile(..., UNREADABLE)`: a discovered file disappears, cannot be stated, cannot be opened, or fails during sampling; recoverable.
- `SkippedDirectory(..., UNREADABLE)`: a descendant directory disappears or cannot be enumerated; recoverable and visible.

Raw `OSError`, `RuntimeError`, and internal implementation exceptions do not cross the public API. Recoverable races are logged at DEBUG. A root failure is logged without repository content or raw sensitive path fragments beyond the caller-supplied root context.

## 19. Deterministic Output and Path Representation

Internally, paths use `pathlib.Path`. Every public child path is relative to the resolved root and converted with `Path.as_posix()`, yielding `/` separators on Windows and Linux. The root itself is returned once as `str(resolved_root)` because it is a local filesystem location, not a portable citation identifier.

`files`, `ignored`, and `skipped_directories` are independently sorted by `(relative_path.casefold(), relative_path)` before tuple construction. The second key makes ordering deterministic when two case-distinct entries exist on a case-sensitive filesystem. Results never depend on `os.scandir` enumeration order or file creation order.

## 20. Counter Semantics

`total_files_seen` counts regular files and file-like symlink entries encountered after ignored-directory pruning. It excludes directories, directory symlinks, contents of pruned directories, and contents of unreadable directories.

`included_files == len(files)` and `ignored_files == len(ignored)`. The required invariant is always:

```text
total_files_seen == included_files + ignored_files
```

`skipped_directories` is deliberately outside this equation. A regular file that disappears after discovery still contributes one seen and one ignored entry with `UNREADABLE`. Ignored dependency-directory contents do not inflate counts because they are never discovered.

Module 1's `RepositoryInfo.total_files` has different semantics: it counts working-tree regular files except `.git` without Module 2's pruning/filtering. The two values are not expected to match and documentation must say so.

## 21. Logging

The module uses `logging.getLogger(__name__)`. INFO logs cover scan start, validated root, and aggregate completion. Pruned directories and per-file exclusions are DEBUG. Descendant traversal problems are WARNING only when they make the inventory partial; transient per-file problems remain DEBUG and are represented in the result.

Logs never contain file contents, sampled bytes, credential values, environment values, or exception representations likely to expose them. Sensitive filenames may be represented by reason and relative path only at DEBUG; no sensitive path is logged at INFO.

## 22. Performance and Memory

Traversal is iterative with `os.scandir`, avoiding Python recursion limits and allowing ignored directories to be pruned before descent. Entries are processed one at a time; only compact metadata results are retained. Cheap checks precede `stat`, and size precedes prefix reading. At most 8192 bytes per eligible file are held for binary detection.

Sorting is deferred until traversal completes, giving `O(n log n)` ordering over reported entries. Returning the complete included and ignored inventory necessarily costs `O(n)` memory for examined files. Pruned-directory reporting stays bounded to one record per pruned root rather than one per unseen descendant. No complete contents, hashes, syntax trees, or chunks are stored.

## 23. Module 3 Output Contract

For each included file, Module 3 receives a stable repository-relative POSIX path, original filename, normalized extension, language hint, high-level category, and byte size. `FileInventory.repository_path` provides the resolved local root needed to locate it.

Module 3 must treat the inventory as a snapshot, not a capability guarantee. Before opening a file it must reconstruct the path from root plus relative path, verify containment, reject links, and handle deletion/replacement races. Module 2 does not parse source structure, determine exact encoding, or guarantee a file remains unchanged after the scan.

## 24. Testing Strategy

Implementation will use TDD in a separate approved plan. `tests/test_file_scanner.py` will use `tmp_path` and synthetic trees with no network, GitHub, Git repository, or Module 1 dependency. Pure filter/classifier behavior will be parameterized; filesystem behavior will use real temporary files and narrowly scoped mocks only for resolution/permission races that cannot be induced portably.

Required coverage includes:

- valid root, missing root, file-as-root, resolution failure, and root enumeration failure;
- simple source/docs scan and exact counter invariant;
- complete pruning of `.git`, `node_modules`, virtual environments, caches, and build output;
- component-only directory matching (`builder`, `distribution`, and `targeting` remain eligible paths);
- representative language mappings for Python, Java, JavaScript, TypeScript/TSX, C/C++, C#, Go, Rust, Kotlin, and special filenames;
- TEST, DATABASE, BUILD, CONFIG, DOCUMENTATION, SOURCE, and OTHER_TEXT classification plus precedence such as `tests/config.json`;
- manifests included while lockfiles are ignored;
- actual `.env` variants and obvious key files excluded without calling `open`, while `.env.example`, `.env.sample`, and `.env.template` remain included;
- misleading binary `.txt`, UTF-8 Unicode, BOM text, likely legacy text, malformed binary, empty files, and exact 30% control threshold;
- oversized candidates ignored before `open`, boundary-size inclusion, and invalid configuration;
- minified files, unsupported binary/media/archive/database formats, `.svg`, and unknown textual extensions;
- all symlinks ignored, escape links never followed, broken links, and junction/reparse behavior where supported;
- disappearing/unreadable files and descendant directories remain recoverable, while root failures are fatal;
- random creation order yields sorted case-stable POSIX relative paths;
- ignored-reason precedence cases and exact enum values;
- `total_files_seen == included_files + ignored_files`, with pruned contents absent from all file counts;
- public package exports and convenience-function delegation;
- no repository content execution or external dependency use.

The later implementation plan should split these into focused tests and small implementation increments; this design phase does not create tests or production code.

## 25. Security Constraints

Repository data is never trusted. Module 2 may enumerate directories, inspect names and metadata, and read a bounded binary prefix. It must never:

- follow links or leave the resolved root;
- import repository modules or source `.env` files;
- execute scripts, binaries, Git hooks, package managers, builds, tests, containers, or shell commands;
- evaluate or deserialize configuration files;
- parse files with unsafe object loaders;
- call Git, GitHub, an LLM, a network service, or a database;
- log file contents or secret values.

Name-based secret exclusion is defense in depth, not comprehensive secret detection. A later stage must continue to treat included content as sensitive untrusted input.

## 26. README Changes for the Implementation Phase

The implementation phase will extend, not replace, the existing README. It will retain Module 1 documentation and add Module 2 purpose, usage, public models, categories, common pruned directories, supported types, size and binary filtering, sensitive-file behavior, security constraints, counter semantics, configuration, and known limitations.

No README change is made during this design-only phase because the feature does not exist yet.

## 27. Explicit Non-Goals

Module 2 does not implement AST or Tree-sitter parsing, symbols, functions/classes, imports, dependency graphs, call graphs, chunking, embeddings, vector storage, semantic search, RAG, LLM/Gemini calls, Git history, runtime evidence, stack traces, test execution, dependency installation, builds, configuration evaluation, API/frontend/authentication work, private repositories, secret scanning, encoding normalization, content hashing, generated-code inference beyond fixed minified filenames, or any Module 3+ behavior.

## 28. Resolved Design Questions and Trade-offs

- **Absolute paths:** only the inventory owns the resolved root; files expose stable relative POSIX paths.
- **Symlinks:** all file and directory links are ignored in V1, including links whose apparent target is internal.
- **Unsupported text:** unknown types are excluded rather than becoming `OTHER_TEXT`; the latter is reserved for explicitly supported text formats.
- **Binary heuristic:** BOM-aware decoding, NUL detection, UTF-8 decoding, then a fixed 30% control-character fallback; no heavyweight dependency.
- **SVG:** excluded as `UNSUPPORTED_TYPE` despite being XML text.
- **Environment files:** only explicit example/sample/template forms are included; actual environment variants are excluded before content access.
- **Case sensitivity:** rule matching is case-insensitive for portable classification; original spelling is preserved.
- **Hidden files:** hidden status alone has no effect; useful dotfiles and `.github` remain eligible, while named ignored directories and sensitive files are excluded.
- **Read failures:** individual files and descendant directories are recoverable and reported; inability to validate or enumerate the root is fatal.
- **Pruned directory transparency:** one `SkippedDirectory` record represents a pruned subtree, avoiding misleading per-file counts and unbounded ignored-file objects.
- **Configuration:** only maximum size and sample size are public in V1; rule customization waits for demonstrated need.

There are no Critical or Important unresolved design questions. Windows link tests remain a required implementation verification item, but the policy is resolved: every symlink or detectable reparse point is skipped and never followed.
