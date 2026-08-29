# TraceRAG

## Module 1 — Repository Loader

The Repository Loader accepts a public GitHub repository root URL, validates and normalizes it, shallow-clones the repository into a controlled workspace (or safely reuses the matching clone), and returns basic Git and filesystem metadata as `RepositoryInfo`.

It deliberately does **not** scan or filter source files, parse ASTs, analyze dependencies or Git history, create embeddings, access a vector database, call an LLM, install dependencies, or execute any repository code. Those concerns belong to later TraceRAG modules.

### Requirements

- Python 3.11 or newer
- Git available on `PATH`

Install the small dependency set:

```bash
python -m pip install -r requirements.txt
```

### Python usage

```python
from backend.repository_loader import RepositoryLoader

loader = RepositoryLoader()
info = loader.load("https://github.com/pallets/flask")

print(info.repository_name)
print(info.current_commit)
print(info.local_path)
```

Repositories are stored deterministically under `workspace/{owner}_{repository}`. Loading the same URL again reuses an existing clone only when its `origin` identifies the same GitHub repository; it does not fetch or update it.

### Tests

Run the offline unit suite:

```bash
python -m pytest -q
```

Run the optional network-dependent integration test explicitly:

```bash
python -m pytest -q -m integration
```

### Manual check

```bash
python scripts/test_repository_loader.py https://github.com/pallets/flask
```

Example output:

```text
TraceRAG Repository Loader
==========================

Status:              SUCCESS
Repository:          pallets/flask
URL:                 https://github.com/pallets/flask
Branch:              main
Commit:              8bd71fa00000...
Files:               814
Size:                4.2 MB
Local Path:          workspace/pallets_flask
Existing Clone:      No
```

### Security

Cloned contents are untrusted input and are treated strictly as data. The loader uses argument-based Git subprocess calls with a timeout, a fresh empty hooks directory, and isolated system/global Git configuration. It never imports cloned Python modules, runs scripts or package managers, installs repository dependencies, invokes builds/tests, or executes Git hooks. Existing workspace data is never overwritten or silently deleted.

**Current limitation:** Only public GitHub repositories are supported. No token, API key, OAuth flow, or private-repository authentication is implemented.

## Module 2 — File Scanner & Filter

The File Scanner & Filter accepts an existing local repository directory and returns a deterministic `FileInventory` of useful text files. It prunes dependency, cache, and build directories before descent; excludes sensitive, unsupported, oversized, minified, and likely binary files; and classifies included files as source, test, configuration, database, documentation, build metadata, or other text.

Module 2 is independent of GitHub and can scan any accessible directory. Its normal integration with Module 1 is:

```python
from backend.file_scanner import FileScanner
from backend.repository_loader import RepositoryLoader

repository = RepositoryLoader().load("https://github.com/pallets/flask")
namespace = "tracerag-repository-v1:github:" + repository.repo_url.casefold()
inventory = FileScanner(max_file_size_bytes=1_000_000).scan(
    repository.local_path,
    repository_namespace=namespace,
)

for file in inventory.files:
    print(file.relative_path, file.language, file.category.value)
```

`FileScanner` defaults to a 1,000,000-byte maximum file size and an 8192-byte binary sample. Both limits are configurable positive integers. Files larger than the configured maximum are rejected from metadata alone and are never opened. Eligible files are read only once for a bounded prefix; Module 2 never loads complete contents merely to classify them.

The optional `repository_namespace` is an opaque pipeline identity supplied by the caller and retained unchanged on `FileInventory`. Existing calls default it to `None`. Module 2 does not import Module 1, parse or normalize repository URLs, or construct this value itself.

The intentional text allowlist covers common programming languages, JSON/YAML/TOML/XML and related configuration, SQL/Prisma, Markdown/reStructuredText/plain text, build manifests, and useful special files such as `Dockerfile`, `Makefile`, and `.gitignore`. Common dependency lockfiles are excluded while their manifests remain included. Unknown types and SVG files are excluded by default.

Directories such as `.git`, `node_modules`, `venv`, `.venv`, caches, `dist`, `build`, `target`, `vendor`, `bin`, and `obj` are pruned by complete case-insensitive path component. `.github` and useful hidden configuration files are not ignored merely for being hidden.

Actual environment files and obvious key/credential filenames are excluded without reading their contents. `.env.example`, `.env.sample`, `.env.template`, and their prefixed equivalents remain eligible configuration templates. This conservative filename policy is defense in depth, not a comprehensive secret scanner.

All symbolic links and detectable Windows reparse points are excluded and never followed. When non-following metadata identifies a directory-like reparse entry, it is reported in `skipped_directories`; an ordinary POSIX link whose target kind cannot be known safely is reported as an ignored file with reason `SYMLINK`.

Included, ignored, and skipped-directory collections are independently sorted by repository-relative POSIX path. The counters mean:

```text
total_files_seen == included_files + ignored_files
```

Pruned directory contents are never discovered and do not inflate the counters. Module 1's `RepositoryInfo.total_files` uses broader working-tree counting semantics, so it is not expected to equal Module 2's `total_files_seen`.

### Module 2 security and limitations

Repository contents remain untrusted static data. The scanner never imports repository modules, evaluates configuration, reads environment values, executes Git or shell commands, invokes package managers/builds/tests, installs dependencies, calls a network service or LLM, or parses ASTs. It uses only the Python standard library.

An inventory is a snapshot. Files may be replaced after scanning, so later consumers must reconstruct paths from `repository_path` plus `relative_path`, repeat containment/link checks, and handle filesystem races. Module 2 provides a deterministic language hint but does not promise a complete-file encoding, parse source structure, detect every secret, infer all generated code, or implement Module 3+ behavior.

## Module 3 — Code Parser

The Code Parser consumes an existing Module 2 `FileInventory` and returns an immutable, deterministic `CodeParseInventory`. It considers only the inventory's source and test entries, reads and parses them one file at a time, and does not rescan repository directories.

Continuing from the Module 1 and Module 2 example:

```python
from backend.code_parser import CodeParser
from backend.file_scanner import FileScanner

inventory = FileScanner().scan(
    repository.local_path,
    repository_namespace=namespace,
)
parsed = CodeParser().parse_inventory(inventory)
```

The equivalent convenience API is `parse_code_inventory(inventory)`. Both APIs accept only a `FileInventory`; they do not accept a repository URL or path in place of that inventory.

`CodeParseInventory.repository_namespace` copies the scanner inventory value unchanged. Each `ParsedFile.source_sha256` is lowercase SHA-256 over the exact complete bytes returned by the hardened reader, including an original UTF-8 BOM and original LF or CRLF bytes. The digest is present for successful and partial parses and for failures occurring after a verified read; it is `None` when no verified source buffer was obtained. No source bytes or decoded source text are retained in the public result.

`SafeSourceReader`, `SourceBuffer`, and `SourceReadError` are a supported internal cross-module boundary imported directly from `backend.code_parser.reader`. They intentionally remain outside the top-level `backend.code_parser` public API.

### Supported syntax and outcomes

Version 1 supports exactly these installed grammar modes:

- Python (`.py`)
- Java (`.java`)
- JavaScript/JSX (`.js` and `.jsx`)
- TypeScript (`.ts`)
- TSX (`.tsx`)

Grammar selection validates the inventory's language hint and extension together. A contradictory pair or another source/test language is a normal unsupported-language skip. Configuration, build, database, documentation, and other non-code categories are outside the parsing request rather than reported as skipped.

Every requested source/test file has exactly one mutually exclusive outcome: `SUCCESS`, `PARTIAL`, `FAILED`, or skipped as unsupported. A clean parse is `SUCCESS`. A parse that retains trustworthy structural metadata while also reporting syntax, missing-node, or extraction issues is `PARTIAL`. A read, containment, identity, decoding, parser, or extraction failure—or malformed syntax with no trustworthy structural item—is `FAILED`. Recoverable failures stay isolated to their file, and issue messages do not expose source text or raw parser exceptions.

Each parsed file can contain symbols, parameters, declared types and defaults, normalized modifiers, direct inheritance/implementation syntax, static import evidence, syntactic call sites, lexical caller names, and issues. Defaults, annotations, callee expressions, imports, and type names are captured only as bounded syntax; they are never evaluated or resolved. Comments and docstrings are not retained.

A `SourceLocation` always addresses the original file bytes as `[start_byte, end_byte)`: the start byte is inclusive, the end byte is exclusive, lines are 1-based, and columns are 0-based UTF-8 byte columns. UTF-8 BOM adjustment preserves those original-byte coordinates. These are byte columns, not Unicode character or display columns.

### Dependencies and offline boundary

Module 3 uses a reviewed, exact compatibility set of installed Tree-sitter wheels:

```text
tree-sitter==0.25.2
tree-sitter-python==0.25.0
tree-sitter-java==0.23.5
tree-sitter-javascript==0.25.0
tree-sitter-typescript==0.23.2
```

Package installation is a deployment/development step. Normal parsing is offline: it uses only these installed wheels, performs no network access, and performs no runtime grammar downloads or compilation. The analyzed repository cannot select a grammar module or native library.

Treat the five pins as one compatibility unit when updating them. Change all five in one reviewed change, create a clean environment, install the proposed exact set, run `tests/test_code_parser_dependencies.py`, and then run all Python, Java, JavaScript/JSX, TypeScript, and TSX fixtures. Stop and revert the update if grammar import, initialization, ABI/API use, expected root nodes, syntax-error behavior, or any language fixture regresses; do not silently change versions or parser APIs.

### Security and scope boundaries

Repository contents remain untrusted static data. Module 3 does not execute repository code, dynamically import repository modules, evaluate decorators/defaults/annotations, run shell commands, package managers, builds, or tests, deserialize repository objects, call Git, a network service, or an LLM, or read grammars from the repository. It repeats containment, link/reparse-point, regular-file, size, exact-read, identity, and strict UTF-8 checks. When the platform cannot provide the required non-following filesystem primitive or stable identity evidence, parsing fails closed for the affected file.

Processing is deliberately one file at a time. The result retains immutable structural metadata only: no complete source bytes or text and no Tree-sitter tree or node is retained after that file completes.

Version 1 performs no cross-file resolution of imports, calls, symbols, base types, or implementations. It builds no dependency, inheritance, or call graph and defines no repository-global symbol IDs. Module 4 and later concerns are explicit non-goals: no chunking, no RAG, no embeddings, vector database, retrieval, graph construction, API, authentication, or frontend is implemented here.
