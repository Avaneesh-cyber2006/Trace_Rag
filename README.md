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

## Module 4 — Code Chunker

The Code Chunker consumes the matching Module 2 `FileInventory` and Module 3 `CodeParseInventory`. Both inventories must have the same normalized repository path and the same nonempty opaque repository namespace. Successful and partial parsed files must also carry Module 3's exact original-byte `source_sha256` fingerprint.

```python
from backend.code_chunker import CodeChunker
from backend.code_parser import CodeParser
from backend.file_scanner import FileScanner

inventory = FileScanner().scan(
    repository.local_path,
    repository_namespace=namespace,
)
parsed = CodeParser().parse_inventory(inventory)
chunks = CodeChunker().chunk_inventory(inventory, parsed)
```

The equivalent convenience API is `chunk_code_inventory(inventory, parsed)`. The default target is 4,096 bytes and the hard maximum is 8,192 bytes. These are source-byte limits, not tokenizer limits.

Module 4 re-reads each processable file only through the supported `backend.code_parser.reader.SafeSourceReader` boundary. It hashes `SourceBuffer.original_bytes` and refuses stale snapshots before trusting structural locations. Each retained chunk is one exact contiguous original-byte slice:

```python
chunk.content.encode("utf-8") == original_bytes[
    chunk.location.start_byte:chunk.location.end_byte
]
```

Callable and constant symbols are primary chunks. Leaf types may be whole chunks; parents with selected descendants contribute only non-overlapping residual context. Successful files retain remaining non-whitespace source as file context. Partial files never receive arbitrary whole-file fallback, and failed parsed files are not re-read and produce no chunks.

`chunk_id` hashes versioned repository-namespaced structural provenance, so equivalent clones have stable identities while different repositories remain distinct. `content_hash` independently hashes the exact chunk bytes. Equal content hashes do not remove or merge provenance records. Fragment indices, locations, file order, chunk order, issues, and inventory counters are deterministic.

Repository source remains untrusted static data. Module 4 never executes or imports repository code, constructs a second parser, reads around the hardened reader, runs Git/shell/package/build/test commands, accesses the network, evaluates syntax, or loads repository configuration or plugins. It does not implement tokenization, embeddings, indexing, retrieval, RAG, graph resolution, API/frontend behavior, or any Module 5+ concern.

## Module 5 — Embedding & Vector Store

Module 5 consumes a Module 4 `CodeChunkInventory`, publishes a persistent semantic index for its exact repository namespace, and returns low-level semantic neighbors with exact source evidence. It validates the inventory without rereading the repository. Core indexing and search use the `EmbeddingProvider` and `VectorStore` protocols; Gemini and Chroma are replaceable adapters.

### Construction and verified configuration

The [dependency capability gate](docs/superpowers/verification/module-5-dependency-capability-gate.md) verified Python 3.12.13 on Windows and these exact direct pins, installed with the existing requirements:

```text
google-genai==2.22.0
chromadb==1.5.9
```

The selected embedding space is provider `gemini`, model `gemini-embedding-001`, 3072 dimensions, and compatibility version `gemini-embedding-001-retrieval-3072-v1`. Documents use `RETRIEVAL_DOCUMENT`; queries use `RETRIEVAL_QUERY`. The adapter validates this explicit configuration eagerly. Supply exactly one of `api_key` or a trusted, preconfigured `client`; client construction makes no embedding request.

Continuing with the Module 4 `chunks` inventory above, the application supplies credentials and a persistence directory outside the analyzed repository:

```python
import os
from pathlib import Path

from backend.embedding_vector_store import (
    ChromaVectorStore,
    GeminiEmbeddingProvider,
    SemanticIndexer,
    SemanticSearcher,
)

# These variables are injected by the application's external configuration.
provider = GeminiEmbeddingProvider(
    api_key=os.environ["TRACERAG_GEMINI_API_KEY"],
    model="gemini-embedding-001",
    dimensions=3072,
    compatibility_version="gemini-embedding-001-retrieval-3072-v1",
    max_batch_size=1,
)
persistence_root = Path(os.environ["TRACERAG_VECTOR_STORE_ROOT"])
store = ChromaVectorStore(persistence_root=persistence_root)
result = SemanticIndexer(provider, store).synchronize(chunks)
matches = SemanticSearcher(provider, store).search(
    repository_namespace=chunks.repository_namespace,
    query_text="Where are repository paths validated?",
    top_k=5,
)
```

`TRACERAG_VECTOR_STORE_ROOT` is an application example convention; the store receives the path explicitly and does not read environment variables. There is no default persistence directory. Keep it on a local filesystem outside the analyzed repository; the application owns that placement policy. Reopening the same root after the owning process exits discovers the explicitly published index. Chroma receives external embeddings with `embedding_function=None` and anonymized telemetry disabled.

The verified Gemini batch capacity is one document per request. Both a complete rendered document and an unchanged query are limited to **1,536 UTF-8 bytes**, a conservative preflight bound below the model's 2,048-token ceiling. Oversized documents raise `EmbeddingDocumentTooLarge`; oversized queries raise `EmbeddingInvalidRequestError`. Nothing is silently truncated, summarized, or automatically rechunked. Module 4's default 4,096-byte target and 8,192-byte maximum do not guarantee that a chunk fits this smaller limit, especially after document metadata is added; callers must prepare a suitable inventory or handle the typed size failure.

`SemanticSearcher` defaults to `max_query_chars=16_384` and `max_top_k=100`, configurable positive integers. Queries must contain non-whitespace text and are passed unchanged. `top_k` must be an integer other than `bool`, between 1 and the configured maximum. The Gemini byte limit applies in addition to the core character limit. Indexing and search accept an optional `RetryPolicy` from `backend.embedding_vector_store.retry`: defaults are 3 attempts, 0.25-second initial delay, multiplier 2, and a 2-second delay cap with an injectable sleeper. Only typed rate-limit and transient provider failures are retried; permanent provider errors and store operations are not blindly retried.

### Documents, reuse, and publication

The deterministic document version is `tracerag-embedding-document-v1`. Its text consists of the version plus LF, compact Unicode JSON with exactly `chunk_kind`, `language`, `parent_qualified_name`, `qualified_name`, `relative_path`, and `symbol_kind` in that order, then `\n---TRACERAG-SOURCE---\n` and exact `CodeChunk.content`. Optional values are JSON `null`; the source suffix preserves original newlines, whitespace, and Unicode. Absolute paths, namespace, hashes, IDs, and provider configuration are excluded from this embedding input.

Vector reuse requires equal chunk ID, content hash, document version, and complete `EmbeddingModelIdentity`. New or updated chunks alone are embedded; deleted chunks disappear from the next complete index. A changed identity or document version disables all reuse. A compatible no-op returns `IndexSyncStatus.UNCHANGED` with zero provider calls, vector writes, candidate creations, or publications. Every chunk is counted as reused and all mutation counters are zero.

A first empty inventory publishes a valid empty index with `SUCCESS`, and replacing a nonempty index with an empty inventory deletes the previous logical records without embedding. Repeating a compatible empty index returns `UNCHANGED`. Search of a compatible empty index returns `()` without a query embedding; a never-indexed namespace raises `RepositoryIndexNotFound`. An incompatible provider identity raises `EmbeddingSpaceMismatch` before query embedding, including for an active empty index.

Synchronization builds and validates a complete private candidate. The checksummed durable active pointer is replaced with same-filesystem `os.replace` after flushing and syncing its temporary file; that replacement is the publication commit point. Failures before publication preserve the prior searchable index and raise typed errors. An uncertain publication outcome is resolved by rereading the pointer; success requires proof that the complete candidate committed. There is no partial success. Cleanup failure after commit leaves the new index authoritative. Restart never infers authority from timestamps or collection ordering, and corrupt or incompatible storage fails closed without automatic repair or migration.

Chroma documents store exact chunk content and metadata preserves provenance. Its embedding field is the sole persisted vector authority: validated provider values receive checked finite IEEE-754 binary32 projection before insertion, without clipping or normalization. Exact Python-float round-trip is not guaranteed. There is no vector mirror in metadata, control files, or another per-chunk manifest. The storage representation belongs to `tracerag-chroma-schema-v1` and does not change the provider compatibility identity.

### Search and process ownership

Search resolves one immutable active snapshot and remains read-only through concurrent publication. Results are a tuple of at most `top_k` unique `VectorSearchResult` values, all from the complete requested namespace, containing exact stored source. Invalid records fail the operation rather than being silently filtered. Ordering is `(-score, relative_path.casefold(), relative_path, chunk_id)`.

Chroma uses cosine distance `distance = 1 - cosine_similarity` over persisted vectors. The public transformation is `score = 1 - (distance / 2)`: identical, orthogonal, and opposite vectors score 1.0, 0.5, and 0.0 respectively. Scores are finite in `[0, 1]`, with higher meaning closer; they are not probabilities. Finite distance error within absolute `1e-6` of an endpoint is treated as 0 or 2; values farther outside `[0, 2]` are corruption. V1 has no universal score cutoff.

V1 supports single-process ownership of each local persistence root; multi-process reads/writes sharing that root are unsupported and competing process ownership is rejected with `VectorStoreConfigurationError`. In-process adapters share the root's client and guards. One writer per exact namespace is admitted from inspection through publication resolution; a concurrent same-namespace synchronization raises `EmbeddingVectorStoreConfigurationError`. Different namespaces have independent guards. Readers retain their old snapshot during candidate construction/publication, and cleanup waits for those readers. This provides no distributed locking or network-filesystem guarantee.

### Public API, security, and tests

`backend.embedding_vector_store` explicitly exports the seven public values (`EmbeddingModelIdentity`, `EmbeddingVector`, `EmbeddingDocument`, `VectorRecord`, `IndexSyncStatus`, `IndexSyncResult`, `VectorSearchResult`), the typed exception hierarchy rooted at `EmbeddingVectorStoreError`, the two protocols, both adapters, `SemanticIndexer`, and `SemanticSearcher`. Provider/store subpackages export only their protocol and adapter. Internal snapshots, candidates, stored records, raw store-search results, collection/generation details, SDK clients, and retry machinery are not top-level public exports. Only `providers/gemini.py` imports the Gemini SDK; only `stores/chroma.py` imports Chroma.

Credentials are explicit external application input through `TRACERAG_GEMINI_API_KEY` in this example; Module 5 does not discover SDK credentials or load repository `.env` files or other repository configuration. Repository contents remain untrusted static data and never select a client, model, path, plugin, or executable. Module 5 does not run repository code, shell commands, builds, tests, or package managers. Embedding intentionally sends rendered source to the configured provider and persists exact source locally; the caller controls which inventories are submitted. Normal diagnostics and public errors omit secrets, authorization headers, source text, embedding documents, vectors, and raw provider/store responses. Exact evidence remains available in result `content` as required by the API.

The default test selection is offline and excludes both networked integration markers, even if live environment guards are set:

```bash
python -m pytest -q
python -m pytest -q -m "not integration and not gemini_live"
```

The optional real Gemini check requires externally supplied `TRACERAG_GEMINI_API_KEY`, `TRACERAG_RUN_GEMINI_LIVE=1`, and explicit selection:

```bash
python -m pytest tests/integration/test_gemini_embeddings_live.py -q -rs -m gemini_live
```

It skips if either guard is absent, sends one small synthetic document and one query, and does not persist returned vectors or print secrets/responses. Its private logging check temporarily changes process-global logging, so run it alone. No real Gemini request is required for normal verification.

### Modules 6–10 non-goals

Module 5 does not implement dependency/call graphs or graph persistence (Module 6); Git-history and temporal evidence (Module 7); runtime, log, error, or stack-trace ingestion (Module 8); BM25, lexical/hybrid retrieval, graph expansion, relevance thresholds, fusion, or reranking (Module 9); or LLM context assembly, answering, causal reasoning, citation presentation, API, or frontend behavior (Module 10).
