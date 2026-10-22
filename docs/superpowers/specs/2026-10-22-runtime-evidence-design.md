# Module 8 — Runtime Evidence Ingestion Design

Date: 2026-10-22

Status: design for independent review. This document defines behavior and
boundaries only. It creates no production package, tests, dependency changes,
implementation plan, or Module 9–10 work.

## 1. Purpose and scope

Module 8 converts caller-supplied runtime, log, error, and stack-trace
artifacts into a deterministic, immutable `RuntimeEvidenceInventory` that can
be associated with a Module 6 `CodeGraphInventory` and later consumed by
Modules 9–10.

The module is deliberately **ingestion-only**. It parses evidence that was
produced elsewhere. It does not run, instrument, attach to, sandbox, or modify
the analyzed application or repository.

```text
caller-owned capture artifacts (bytes)
        |
        v
strict format adapters + bounded parsing
        |
        v
normalized events, stack frames, issues, provenance
        |
        +---- optional Module 6 static graph ----> line-level candidate association
        |
        v
RuntimeEvidenceInventory (immutable)
```

The design priorities, in order, are security, evidence fidelity, determinism,
simple interfaces, testability, portability, and bounded resource use. V1 uses
only the Python standard library and supports Python 3.11+ on Windows and
Linux.

## 2. Trust position and evidence-versus-causality boundary

Runtime artifacts are untrusted data. They may be malformed, adversarial,
incomplete, stale, or sensitive. Module 8 treats every field as a claim or an
observation about the artifact, never as proof about the analyzed system.

The inventory distinguishes these categories explicitly:

| Category | Examples | Module 8 treatment |
|---|---|---|
| Static fact | Module 6 file paths, symbol nodes, source ranges | Accepted only from a validated graph snapshot; copied, never inferred from runtime text |
| Artifact fact | exact artifact bytes, byte count, SHA-256, parse outcome | Computed by Module 8 from the submitted bytes |
| Git fact | none | Module 8 never invokes Git and never verifies history |
| Observation | log message, level text, timestamp text, trace/span IDs, stack frame text | Retained as reported, bounded, and never interpreted as verified execution truth |
| User claim | declared revision, declared capture completeness, declared capture time | Labeled as declared; never verified or promoted to fact |
| Inference | path suffix/basename matching, stack-frame parsing, graph containment association | Deterministic heuristic with an explicit basis/outcome; never presented as proof |
| Causal claim | none | Forbidden in V1 |

Required boundaries:

- A stack frame does not prove that the frame caused the error. It records
  the call order reported by the artifact.
- A frame associated with a symbol does not prove that the symbol executed in
  the graph revision, or that the runtime code matches the graph snapshot.
- Repeated events are occurrences, not a measure of impact, severity, or
  causality.
- Absence of an event is not evidence that an event did not occur.
- Artifact timestamps are opaque text. Module 8 does not build a global
  chronology or infer ordering from them.
- Module 8 does not link to Module 7 history, a commit, or a branch as proof.
  A caller may supply `declared_revision`; it remains an unverified claim.

The graph binding is explicitly unversioned. Module 6 exposes no commit-bound
graph identity, and Module 8 must not invent one. When a graph is supplied,
the inventory records `GraphBinding.NAMESPACE_MATCHED_UNVERSIONED`. When no
graph is supplied, it records `GraphBinding.NOT_PROVIDED`.

## 3. Considered approaches and selected architecture

### Selected: ingestion-only, bytes-in

The caller acquires artifacts under its own collection policy and passes exact
bytes to Module 8. Module 8 parses only those bytes, optionally associates the
result with an already-built Module 6 graph, and returns immutable evidence.

This matches the existing project boundary: Modules 1–7 never execute analyzed
repository code. Runtime production is the caller's responsibility. A CI job,
test harness, application, or observability pipeline can run code under its own
sandbox and emit one of Module 8's bounded canonical formats.

### Rejected: controlled execution by Module 8

Running repository tests, binaries, containers, debuggers, or profilers would
require a new threat model: operating-system or container isolation, network
policy, filesystem policy, credential injection, process and memory limits,
artifact capture, cleanup, and failure containment. None of those guarantees
can be established by this module's current scope. A subprocess with a timeout
is not a sandbox. V1 does not claim otherwise.

### Rejected: optional instrumentation hooks inside Module 8

Instrumentation requires modifying or injecting into application code and then
running it. It has the same execution-boundary problem plus language/runtime
specificity, version coupling, and build-system coupling. It is not an
ingestion concern.

### Rejected for V1: staged hybrid

A staged hybrid that first ingests then later executes is still an execution
system. Adding a hidden or opt-in executor would enlarge the attack surface
and make the public contract ambiguous. Controlled execution or instrumentation
may be proposed later only as a separately reviewed design with its own
sandbox, credential, network, filesystem, and cleanup contracts. It must not be
smuggled in as a Module 8 flag.

### Consequences

- Module 8 has no subprocess, shell, debugger, tracer, browser automation,
  network, database, Git, embedding, or LLM dependency.
- Module 8 does not provide a sandbox because it does not cross the execution
  boundary. This is a resolved limitation, not an unimplemented feature.
- Artifact acquisition, retention, access control, and pre-ingestion
  sanitization belong to the caller. Module 8 documents that boundary and does
  not duplicate or weaken it.

## 4. Existing contracts and compatibility

Baseline: `main` at `6f25511af8fa1615342a6be3083b09ac3e73d4be`.

Module 8 depends only on the public Module 6 package:

```python
from backend.code_graph import (
    CodeGraphInventory,
    GraphNode,
    GraphNodeKind,
)
```

It does not import Module 6 internals, Module 3 parser internals, Module 4
chunkers, Module 5 providers/stores, or Module 7 Git adapters. It does not
modify any upstream model or behavior.

Relevant Module 6 facts:

- `CodeGraphInventory.repository_namespace` is a nonempty string.
- `nodes` is an immutable tuple of `GraphNode`; FILE nodes have no location,
  and SYMBOL nodes carry a Module 3 `SourceLocation`.
- `SourceLocation.start_line`/`end_line` are one-based; byte columns are
  zero-based UTF-8 byte columns.
- `nodes_by_file` is an immutable mapping from relative POSIX path to nodes.
- The graph is a static snapshot. It is not commit-bound and does not prove
  any runtime behavior.

Module 8 also requires these project-wide conventions:

- Public models are `@dataclass(frozen=True, slots=True)`.
- Public collections are tuples; public indexes are read-only mappings.
- Enums derive from `str, Enum` and serialize by value.
- Public exceptions are a small typed hierarchy; internal exceptions do not
  escape with raw repository-controlled text.
- Canonical identifiers use lowercase SHA-256 over UTF-8 JSON arrays serialized
  with `ensure_ascii=False` and `separators=(",", ":")`. All strings in
  canonical payloads are Unicode scalar-only; a valid JSON surrogate pair is
  decoded to the corresponding scalar, and `surrogatepass` is never used.
- No absolute repository path, local username, machine path, Module 8-generated
  timestamp, random UUID, or Python `hash()` enters artifact, event, or frame
  identity. Content is hashed exactly as supplied, so timestamp or path bytes
  inside an artifact affect its content hash as any other byte does; they are
  never separately retained or interpreted. Event fingerprints may include
  reported timestamp text; they are grouping keys, not identities.
- No new runtime dependency is introduced in V1.

Module 8 must not change Modules 1–7. If implementation discovers a missing
upstream contract, work stops for design review rather than silently extending
an upstream model.

## 5. Public API and ownership

```python
class RuntimeEvidenceIngestor:
    def __init__(
        self,
        config: RuntimeEvidenceConfig | None = None,
    ) -> None: ...

    def ingest(
        self,
        request: RuntimeEvidenceRequest,
        graph: CodeGraphInventory | None = None,
    ) -> RuntimeEvidenceInventory: ...


def ingest_runtime_evidence(
    request: RuntimeEvidenceRequest,
    graph: CodeGraphInventory | None = None,
    config: RuntimeEvidenceConfig | None = None,
) -> RuntimeEvidenceInventory: ...
```

Behavior:

- The constructor validates the complete configuration eagerly and raises
  `RuntimeEvidenceConfigurationError` on any invalid value.
- `ingest` is single-threaded in execution but holds no mutable instance state
  beyond immutable configuration. Separate calls and separate instances cannot
  affect one another. Concurrent calls on one instance are safe because all
  parse state is local.
- The convenience function constructs an isolated default ingestor and
  delegates exactly.
- The graph is optional. Omitting it still ingests evidence, normalizes
  relative paths, and emits events/frames; association is
  `NOT_EVALUATED`.
- A supplied graph must be an exact `CodeGraphInventory` with the same
  repository namespace. Its public structure is validated enough to build a
  safe association index. A mismatch or malformed graph is fatal; it is not
  silently ignored.
- The API accepts exact artifact bytes, not paths. It never opens, stats,
  lists, follows, or deletes files. Artifact acquisition and any containment
  or symlink policy belong to the caller.
- The API has no persistence, cache, temporary workspace, callback, network,
  subprocess, or cleanup parameter. There is nothing for the caller to close.
- Every public record, request, and inventory defines a redacted `__repr__`;
  `RuntimeEvidenceConfig` is exempt because it contains only limits and one
  boolean. A representation may show the type name, stable record IDs,
  format/language enums, counters, booleans, and numeric source positions. It
  never shows raw bytes, free-form text, tag keys or values, function text,
  relative or absolute paths, source names, repository namespace, declared
  claims, trace/span IDs, graph or index contents, or exception text.
  `RuntimeArtifact.__repr__` therefore shows no content and no source name.

The public `backend.runtime_evidence` package exports the two entry points,
`RuntimeEvidenceConfig`, every public enum and dataclass defined in §6, and the
seven exception classes defined in §13.1. It does not export format adapters,
parsers, indexes, association helpers, or their modules. Format adapters,
parsers, indexes, and association helpers are internal.

## 6. Public data model

### 6.1 Enums and configuration

```python
class RuntimeArtifactFormat(str, Enum):
    TEXT_LOG = "text_log"
    JSON_EVENTS = "json_events"
    STACK_TRACE_TEXT = "stack_trace_text"


class RuntimeArtifactStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class RuntimeEventKind(str, Enum):
    LOG = "log"
    ERROR = "error"
    SPAN = "span"


class RuntimeFrameLanguage(str, Enum):
    PYTHON = "python"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    TSX = "tsx"
    UNKNOWN = "unknown"


class CaptureCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class GraphBinding(str, Enum):
    NOT_PROVIDED = "not_provided"
    NAMESPACE_MATCHED_UNVERSIONED = "namespace_matched_unversioned"


class PathMatchBasis(str, Enum):
    EXACT_RELATIVE_PATH = "exact_relative_path"
    UNVERIFIED_RELATIVE_PATH = "unverified_relative_path"
    UNIQUE_PATH_SUFFIX = "unique_path_suffix"
    UNIQUE_BASENAME = "unique_basename"
    EXTERNAL_OR_NON_FILE = "external_or_non_file"
    AMBIGUOUS = "ambiguous"
    UNMATCHED = "unmatched"


class FrameAssociation(str, Enum):
    NOT_EVALUATED = "not_evaluated"
    UNASSOCIATED = "unassociated"
    FILE = "file"
    SYMBOL = "symbol"
    AMBIGUOUS_SYMBOL = "ambiguous_symbol"


class RuntimeIncompleteReason(str, Enum):
    ARTIFACT_FAILED = "artifact_failed"
    ARTIFACT_PARTIAL = "artifact_partial"
    ARTIFACT_SKIPPED = "artifact_skipped"
    EVENT_LIMIT_REACHED = "event_limit_reached"
    FRAME_LIMIT_REACHED = "frame_limit_reached"
    ISSUE_LIMIT_REACHED = "issue_limit_reached"
    TEXT_TRUNCATED = "text_truncated"
```

`RuntimeEvidenceConfig` is frozen and slotted. Every integer is a positive
integer but not `bool`; `retain_event_text` is an exact `bool`.

```python
@dataclass(frozen=True, slots=True)
class RuntimeEvidenceConfig:
    max_artifacts: int = 64
    max_namespace_bytes: int = 256
    max_artifact_bytes: int = 8_388_608
    max_submitted_input_bytes: int = 67_108_864
    max_total_input_bytes: int = 67_108_864
    max_events: int = 50_000
    max_json_nodes: int = 1_000_000
    max_json_number_bytes: int = 1_024
    max_frames_per_event: int = 128
    max_total_frames: int = 200_000
    max_issues: int = 10_000
    max_line_bytes: int = 65_536
    max_source_reference_bytes: int = 1_024
    max_message_bytes: int = 8_192
    max_error_message_bytes: int = 8_192
    max_stack_bytes: int = 65_536
    max_frame_function_bytes: int = 1_024
    max_tags: int = 32
    max_tag_key_bytes: int = 128
    max_tag_value_bytes: int = 512
    retain_event_text: bool = True
```

Cross-field validation requires `max_artifact_bytes <= max_total_input_bytes`,
`max_total_input_bytes <= max_submitted_input_bytes`, and
`max_frames_per_event <= max_total_frames`. The limits are structural,
not wall-clock budgets. There is no processing-time limit because Module 8
performs no external calls and must not use timing-dependent behavior.

Budget justification:

- 64 artifacts covers a CI run split into log shards without encouraging a
  second observability platform inside this module.
- 256 bytes bounds a repository namespace that is included in every artifact,
  event, and frame identity, while allowing ordinary namespaced identifiers.
- 8 MiB per artifact and 64 MiB total bound parsing memory and prevent a
  single runaway log from dominating a request.
- 64 MiB submitted input bounds pre-deduplication hashing; 64 MiB unique input
  bounds retained artifact work. Duplicate submissions consume the submitted
  budget but not the unique budget.
- 1,000,000 JSON nodes accommodates up to 50,000 event objects with several
  fields while rejecting pathological arrays of tiny objects; 1 KiB numeric
  tokens reject pathological integer conversion while allowing ordinary
  numeric metadata. The raw artifact byte cap still bounds the input itself.
- 50,000 events and 200,000 frames retain useful evidence while keeping a
  single immutable inventory manageable in memory.
- 128 frames per event covers ordinary nested traces; deeper traces are
  truncated explicitly.
- 8 KiB messages, 64 KiB lines/stacks, and 64 KiB of stack text are above
  ordinary diagnostic needs and below accidental bulk-data ingestion.
- 10,000 ordinary issues prevents malformed input from generating an unbounded
  issue tuple; at most one additional issue-limit sentinel records that
  truncation occurred.
- 32 tags and the tag-length limits keep structured metadata bounded and
  deterministic.

These defaults are a V1 contract, not a claim about every deployment. The
implementation plan must validate them against representative artifacts before
release; changing them later is an explicit compatibility decision.

### 6.2 Request and artifact inputs

```python
@dataclass(frozen=True, slots=True)
class RuntimeArtifact:
    source_name: str
    format: RuntimeArtifactFormat
    content: bytes
    declared_language: RuntimeFrameLanguage | None = None

    def __repr__(self) -> str: ...  # never includes content


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceRequest:
    repository_namespace: str
    artifacts: tuple[RuntimeArtifact, ...]
    declared_revision: str | None = None
    declared_captured_at: str | None = None
    declared_capture_completeness: CaptureCompleteness = CaptureCompleteness.UNKNOWN
```

`repository_namespace` is a nonempty exact string of at most
`max_namespace_bytes` UTF-8 bytes. It is opaque, case-sensitive, not trimmed,
and must equal the graph namespace when a graph is supplied.

`source_name` is an opaque capture label, not a path. It must be a nonempty
string of at most 128 UTF-8 bytes, with no `/`, `\`, `:`, NUL, C0 control, or
leading/trailing ASCII whitespace. It exists to preserve provenance and to
give duplicate submissions distinguishable names. It never enters artifact
identity.

`content` must be exact `bytes`, not `bytearray` or `memoryview`. A request is
one logical capture context: its `declared_*` fields describe the whole
request. Artifacts from unrelated captures belong in separate requests.
`declared_revision` and `declared_captured_at` are opaque claims of at most
256 and 128 UTF-8 bytes respectively; they are not parsed, normalized, or
verified. They must not contain NUL; other control characters are retained as
data and never logged. `declared_capture_completeness` is also a caller claim
and does not affect ingestion completeness.

Every string in the request, including `repository_namespace`, `source_name`,
`declared_revision`, and `declared_captured_at`, must contain only Unicode
scalar values. Any surrogate code point, paired or unpaired, is invalid and
raises `RuntimeEvidenceValidationError`. No request string is repaired,
scalarized, or encoded with `surrogatepass`.

Artifact deduplication is exact:

- Two submissions are duplicates when `(format, declared_language,
  content_sha256)` are equal.
- Duplicates collapse into one `RuntimeArtifactRecord`; all distinct source
  names are retained in sorted order, and `duplicate_count` counts the
  additional submissions. Repeating the same source name with the same
  deduplication key increases `duplicate_count` but does not duplicate the
  name in `source_names`.
- The same source name with different content, format, or declared language is
  a fatal validation conflict; it would make provenance ambiguous.
- Artifact identity includes the content hash, format, declared language, and
  repository namespace. It excludes source names, so renaming an identical
  artifact does not change its identity.

`STACK_TRACE_TEXT` requires a known `declared_language` (not `None` and not
`UNKNOWN`). Other formats may omit it; if a JSON event supplies `error.stack`
without a known language, the event is retained but the stack is not parsed
and the artifact is partial. `JSON_EVENTS` explicit `error.frames` do not
require a declared language; their frames use `UNKNOWN` when none is supplied.

### 6.3 Events, frames, issues, and artifact records

```python
class RuntimeIssueCode(str, Enum):
    ARTIFACT_TOO_LARGE = "artifact_too_large"
    INVALID_UTF8 = "invalid_utf8"
    NUL_BYTE = "nul_byte"
    LINE_TOO_LONG = "line_too_long"
    MALFORMED_JSON = "malformed_json"
    DUPLICATE_JSON_KEY = "duplicate_json_key"
    INVALID_EVENT_SCHEMA = "invalid_event_schema"
    STACK_LANGUAGE_MISSING = "stack_language_missing"
    STACK_TOO_LARGE = "stack_too_large"
    STACK_UNPARSEABLE = "stack_unparseable"
    INVALID_SOURCE_LOCATION = "invalid_source_location"
    TEXT_TRUNCATED = "text_truncated"
    FRAME_LIMIT_REACHED = "frame_limit_reached"
    EVENT_LIMIT_REACHED = "event_limit_reached"
    ISSUE_LIMIT_REACHED = "issue_limit_reached"


@dataclass(frozen=True, slots=True)
class RuntimeTag:
    key: str
    value: str


@dataclass(frozen=True, slots=True)
class RuntimeStackFrame:
    frame_id: str
    event_id: str
    ordinal: int
    language: RuntimeFrameLanguage
    function_text: str | None
    relative_path: str | None
    line: int | None
    column: int | None
    source_reference_sha256: str | None
    path_match_basis: PathMatchBasis
    association: FrameAssociation
    primary_node_id: str | None
    candidate_node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    event_id: str
    event_fingerprint: str
    artifact_id: str
    sequence: int
    source_line: int | None
    kind: RuntimeEventKind
    timestamp_text: str | None
    level: str | None
    message: str | None
    logger: str | None
    operation: str | None
    trace_id: str | None
    span_id: str | None
    parent_span_id: str | None
    duration_ms_text: str | None
    status: str | None
    error_type: str | None
    error_message: str | None
    tags: tuple[RuntimeTag, ...]
    frames: tuple[RuntimeStackFrame, ...]
    text_truncated: bool
    frames_truncated: bool


@dataclass(frozen=True, slots=True)
class RuntimeEvidenceIssue:
    code: RuntimeIssueCode
    message: str
    artifact_id: str
    event_sequence: int | None
    source_line: int | None


@dataclass(frozen=True, slots=True)
class RuntimeArtifactRecord:
    artifact_id: str
    source_names: tuple[str, ...]
    format: RuntimeArtifactFormat
    declared_language: RuntimeFrameLanguage | None
    content_sha256: str
    size_bytes: int
    status: RuntimeArtifactStatus
    event_count: int
    frame_count: int
    duplicate_count: int
    text_truncated: bool
    frames_truncated: bool
    issue_count: int
```

Field semantics:

- `RuntimeEvent.sequence` is the zero-based event ordinal within one artifact.
  It counts emitted events, not physical lines, and remains contiguous even
  when malformed lines are skipped.
- `RuntimeEvent.source_line` is the one-based physical line where a text or
  JSONL event began. It is `None` for events from a JSON document/array.
- `timestamp_text`, `level`, `status`, `trace_id`, `span_id`,
  `parent_span_id`, `duration_ms_text`, `logger`, `operation`, `error_type`,
  and tags are retained exactly as supplied, bounded by the configuration.
  They are opaque evidence, not parsed types. `duration_ms_text` must match a
  non-negative decimal string; it is not converted to a float.
- `message` and `error_message` are bounded exact text. If truncated, the
  field ends at a valid UTF-8 boundary with a final Unicode ellipsis and
  `text_truncated` is true.
- `tags` are sorted by key. Keys are unique. Values are strings. No nested
  objects, arrays, numbers, or booleans are accepted as tag values.
- `RuntimeStackFrame.ordinal` is the zero-based call order reported by the
  artifact. It is not a causal rank.
- `line` is one-based exactly as reported. `column` is exactly as reported and
  is never normalized to Module 3 byte-column semantics. Either may be `None`
  when the source format does not provide it.
- `relative_path` is a normalized repository-relative POSIX path when one is
  retained. It is graph-verified only for `EXACT_RELATIVE_PATH`,
  `UNIQUE_PATH_SUFFIX`, and `UNIQUE_BASENAME`; a path retained under
  `UNVERIFIED_RELATIVE_PATH`, an `AMBIGUOUS` relative-suffix candidate, or an
  unmatched relative reference is an unverified candidate. Absolute local
  paths, drive letters, UNC paths, and raw stack source strings are never
  stored.
- `source_reference_sha256` is a lowercase SHA-256 over a privacy-preserving
  normalized source reference. It is present exactly when `relative_path` is
  not `None`: a verified graph path, an unverified relative path (including an
  unmatched relative candidate or an ambiguous relative-suffix candidate), or a
  uniquely resolved basename. It is absent for unresolved absolute paths,
  ambiguous or unmatched basename references, ambiguous absolute-suffix
  references, external/non-file sources, invalid sources, and frames with no
  source. It supports grouping without retaining a machine path.
- `path_match_basis` records exactly how `relative_path` was obtained. For a
  frame with no source or an invalid source, it is `UNMATCHED` and both
  `relative_path` and `source_reference_sha256` are `None`;
  `EXTERNAL_OR_NON_FILE` is used only for a recognized external/non-file
  reference.
- `association`, `primary_node_id`, and `candidate_node_ids` record the
  optional graph result. Candidate IDs are unique and sorted lexicographically.
- `FRAME_LIMIT_REACHED` is used for both per-event and global frame loss;
  `frames_truncated` on the event and artifact marks the affected scope.
- Artifact `issue_count` must equal the number of inventory issues carrying
  that `artifact_id`. Issues are stored once in the inventory, not duplicated
  inside artifact records.
- `RuntimeEvidenceIssue.message` is exactly the fixed string selected by
  `code` in the table below. It never contains artifact text, paths, tags, or
  parser exception text.

  | Code | Message |
  |---|---|
  | `ARTIFACT_TOO_LARGE` | `artifact exceeds the configured byte limit` |
  | `INVALID_UTF8` | `line is not valid UTF-8` |
  | `NUL_BYTE` | `NUL byte is not allowed` |
  | `LINE_TOO_LONG` | `line exceeds the configured byte limit` |
  | `MALFORMED_JSON` | `line or document is not valid JSON` |
  | `DUPLICATE_JSON_KEY` | `JSON object contains a duplicate key` |
  | `INVALID_EVENT_SCHEMA` | `event does not match the supported schema` |
  | `STACK_LANGUAGE_MISSING` | `stack cannot be parsed without a known language` |
  | `STACK_TOO_LARGE` | `stack exceeds the configured byte limit` |
  | `STACK_UNPARSEABLE` | `stack contains no recognized frame` |
  | `INVALID_SOURCE_LOCATION` | `source location is invalid` |
  | `TEXT_TRUNCATED` | `text was truncated to the configured limit` |
  | `FRAME_LIMIT_REACHED` | `frame limit was reached` |
  | `EVENT_LIMIT_REACHED` | `event limit was reached` |
  | `ISSUE_LIMIT_REACHED` | `additional issues were omitted` |

- `RuntimeEvidenceIssue.source_line` is the one-based physical artifact line
  most directly associated with the issue when one is known: the offending
  physical line for line-oriented decode or JSONL issues; otherwise the event's
  `source_line` for event- or frame-level issues; and `None` for document-mode
  or artifact-level issues with no physical line. Frame-level issues in a
  `STACK_TRACE_TEXT` block use the block event's `source_line`, because frame
  physical lines are not separately retained. An issue-limit sentinel copies
  the dropped issue's `event_sequence` and `source_line` as specified in §12.
- Frame-level issues are event-scoped. Document-array-element issues are
  artifact-scoped because an invalid element does not become an event. The
  issue model carries no frame ordinal or array index, so multiple frame issues
  with the same code in one event and `source_line`, or multiple document-array
  element issues with the same code, collapse under the deduplication key. This
  is an accepted V1 diagnostic-locality limitation; it never removes event or
  frame evidence.

Model constructors validate local types, enum membership, tuple shapes,
nonnegative counters, lowercase 64-character hexadecimal hashes, nonnegative
sequence/ordinal values, and `RuntimeEvidenceIssue.message` equal to the fixed
mapping above for its `code`. Every public model constructor, including
`RuntimeArtifact` and `RuntimeEvidenceRequest`, validates every string field,
mapping key, and mapping value as Unicode scalar-only. Caller-input models
(`RuntimeArtifact` and `RuntimeEvidenceRequest`) raise
`RuntimeEvidenceValidationError` for invalid caller values; output models raise
`RuntimeEvidenceInvariantError` for invalid internal values. This covers direct
construction of messages, tags, function text, paths, source names, claims,
namespace, and index keys. Cross-model invariants are validated by the ingestor
and by `RuntimeEvidenceInventory.__post_init__` before return. Direct
construction with a mismatched issue message raises
`RuntimeEvidenceInvariantError`.

### 6.4 Inventory

```python
@dataclass(frozen=True, slots=True)
class RuntimeEvidenceInventory:
    repository_namespace: str
    declared_revision: str | None
    declared_captured_at: str | None
    declared_capture_completeness: CaptureCompleteness
    graph_binding: GraphBinding
    event_text_retained: bool
    artifacts: tuple[RuntimeArtifactRecord, ...]
    events: tuple[RuntimeEvent, ...]
    frames: tuple[RuntimeStackFrame, ...]
    issues: tuple[RuntimeEvidenceIssue, ...]
    submitted_artifacts: int
    duplicate_artifacts: int
    total_input_bytes: int
    total_events: int
    total_frames: int
    text_truncated: bool
    events_truncated: bool
    frames_truncated: bool
    issues_truncated: bool
    ingestion_complete: bool
    incomplete_reasons: tuple[RuntimeIncompleteReason, ...]
    events_by_artifact: Mapping[str, tuple[RuntimeEvent, ...]]
    events_by_trace_id: Mapping[str, tuple[RuntimeEvent, ...]]
    events_by_fingerprint: Mapping[str, tuple[RuntimeEvent, ...]]
    frames_by_file: Mapping[str, tuple[RuntimeStackFrame, ...]]
    frames_by_node: Mapping[str, tuple[RuntimeStackFrame, ...]]
```

`events` is the authoritative event tuple. `frames` is the flattened
concatenation of each event's frames in event order. `issues` is the single
authoritative issue tuple. Indexes are derived snapshots and never contain
records that are absent from the authoritative tuples.

`event_text_retained` is copied from the effective configuration and must be an
exact `bool`. It is `False` exactly when metadata-only projection was applied.
It is not an identity input, is not itself a truncation signal, and lets a
consumer distinguish text omitted by policy from a source field that was
naturally absent.

The inventory has no raw artifact bytes, artifact path, machine path,
username, environment variable, credential, raw parser exception, or
timestamp generated by Module 8. Its class constant is:

```python
SCHEMA_VERSION = "tracerag-runtime-evidence-v1"
```

## 7. Identity, provenance, and deduplication

### 7.1 Canonical identity

All identifiers are lowercase 64-character SHA-256 digests of UTF-8 canonical
JSON arrays with `ensure_ascii=False` and `separators=(",", ":")`. JSON `null`
represents an absent optional value. Canonical serialization is defined only
for Unicode scalar-only strings; every public string is validated under that
rule before it can enter an identity or fingerprint payload, and the encoder
never uses `surrogatepass`.

```text
ARTIFACT:
["tracerag-runtime-artifact-v1", repository_namespace,
 format.value, declared_language.value or null, content_sha256]

EVENT:
["tracerag-runtime-event-v1", repository_namespace,
 artifact_id, sequence]

FRAME:
["tracerag-runtime-frame-v1", repository_namespace,
 event_id, ordinal]
```

Artifact identity is content-addressed: exact bytes affect it only through
their SHA-256, and source names do not. Event and frame identity is
occurrence-based: `sequence` and `ordinal` are deliberate parts of identity.
None of the identifiers depends on the graph, source names, parsed/normalized
text fields, paths, timestamps as separate metadata, wall-clock time, process
identity, random values, artifact input order, or global output/index ordering.
IDs are stable for fixed artifact bytes, configuration, and parse outcome;
configuration changes that alter which events or frames are emitted can change
the affected occurrences' IDs. Graph changes, source-label changes, retention
mode, and text-field truncation do not change artifact, event, or frame IDs.

The event fingerprint is a separate grouping key, not an identity:

```text
FINGERPRINT PAYLOAD:
[
  "tracerag-runtime-event-fingerprint-v1",
  repository_namespace,
  kind.value,
  timestamp_text or null,
  level or null,
  message or null,
  logger or null,
  operation or null,
  trace_id or null,
  span_id or null,
  parent_span_id or null,
  duration_ms_text or null,
  status or null,
  error_type or null,
  error_message or null,
  tags as [[key, value], ...],
  frames as [[ordinal, language.value, function_text or null,
              relative_path or null, line or null, column or null,
              source_reference_sha256 or null], ...]
]
```

Tags and frames are emitted in their canonical sorted/ordinal order. The
fingerprint deliberately excludes artifact ID, sequence, source line, path
match basis, graph association, candidate node IDs, and truncation flags. It
includes the repository namespace so identical normalized content in different
repositories does not group together. Because `relative_path` can depend on
graph suffix matching for an absolute reported path, the fingerprint is a
grouping aid and may change if a different graph is supplied. Event, frame, and
artifact IDs remain stable.

Fingerprints are computed from the effective retained fields after
metadata-only projection and text truncation. They are therefore not comparable
across different `retain_event_text` settings or configurations with different
text limits, even though artifact IDs remain stable and event/frame IDs remain
stable for occurrences that are still emitted.
Unresolved absolute and external source references are deliberately not
retained, so fingerprints cannot distinguish frames that differ only in such a
reference; event and frame IDs still distinguish the occurrences.

A fingerprint collision means two events are treated as the same normalized
content for grouping. It does not merge, delete, or rewrite either event.

### 7.2 Provenance

Every event points to exactly one artifact through `artifact_id`. Every frame
points to exactly one event through `event_id` and an ordinal. Every issue
points to exactly one artifact through `artifact_id`. Every artifact record
retains:

- its content SHA-256 and exact byte size;
- its format and declared language;
- all source names under which it was submitted;
- its duplicate count and parse status.

The request-level `declared_revision`, `declared_captured_at`, and
`declared_capture_completeness` are copied unchanged to the inventory and
marked as declared by their names. They are not attached to individual events
and are not verified against Git, a graph revision, or a running process.

### 7.3 Deduplication and non-deduplication

Exact artifact deduplication is mandatory:

- key: `(format, declared_language, content_sha256)`;
- one canonical artifact record per key;
- all distinct source names retained and sorted;
- `duplicate_count` is the number of additional submissions collapsed;
- a source name conflict with different content/format/language is fatal.

Evidence occurrences are not content-deduplicated:

- Repeated identical log lines remain separate events with distinct
  `sequence` values and distinct event IDs.
- Recursive or repeated stack frames remain separate frames with distinct
  ordinals.
- `events_by_fingerprint` groups equal normalized content but does not remove
  occurrences. It exists so a later module can measure repetition or merge
  deliberately.
- No global event or frame deduplication occurs across artifacts.
- Identical issues are deduplicated by `(code, artifact_id, event_sequence,
  source_line)`, keeping the first in deterministic parse order. This prevents
  repeated fixed-template issue spam without changing event evidence.

Artifact deduplication is not a claim that two captures were the same run.
It only states that the exact bytes, format, and language were identical.

## 8. Ordering, completeness, counters, and indexes

### 8.1 Deterministic ordering

- `source_names` within an artifact: `(casefold(), original)`.
- Artifacts: primary source name `(casefold(), original)`, then
  `artifact_id`. The primary name is `source_names[0]`. Source names are
  unique across distinct artifacts, so this order is total.
- Events: artifact order, then `sequence`.
- Frames: event order, then `ordinal`.
- Issues: artifact order, then `event_sequence` with absent last, then
  `source_line` with absent last, then `code.value`, then fixed message.
- Tags: key order.
- Incomplete reasons: lexicographic order of `RuntimeIncompleteReason.value`.
- Index mapping keys: lexical order. Values preserve the global authoritative
  order: events by artifact order then sequence; frames by event order then
  ordinal. They are not re-sorted by the index key.

Input artifact order never affects output. Timestamps never affect ordering.
Module 8 makes no global chronological claim. A caller that needs temporal
ordering must do so explicitly with its own timestamp semantics.

### 8.2 Ingestion completeness versus capture completeness

`ingestion_complete` means Module 8 processed every unique submitted artifact
without losing evidence under the configured limits:

```text
ingestion_complete == (incomplete_reasons == ())
```

It does not mean the capture covered every execution, every service, or every
error. `declared_capture_completeness` is the caller's unverified claim and is
kept separate.

Artifact status is computed from parsing outcomes, not from whether an
individual diagnostic issue was retained. Dropping diagnostic issues at the
inventory issue limit does not by itself change artifact status.

- `COMPLETE`: all artifact bytes were processed and no event, frame, or retained
  text evidence was skipped, truncated, or lost.
- `PARTIAL`: at least one usable event was produced, but at least one evidence
  record was skipped, truncated, or malformed.
- `FAILED`: no usable event was produced and the artifact is not `SKIPPED`.
- `SKIPPED`: the artifact was not parsed because it exceeded the per-artifact
  byte limit or because the event budget was already exhausted. A frame-budget
  exhaustion does not skip later artifacts.

An empty `TEXT_LOG` or `STACK_TRACE_TEXT` artifact, or a `TEXT_LOG` artifact
containing only blank lines, is `COMPLETE` with zero events. An empty or
blank-only `JSON_EVENTS` artifact is the empty JSONL shape and is `COMPLETE`
with zero events. An empty JSON array is also `COMPLETE` with zero events.
None of these outcomes is evidence that no runtime activity occurred.

`incomplete_reasons` is derived exactly:

| Condition | Reason |
|---|---|
| any artifact `FAILED` | `ARTIFACT_FAILED` |
| any artifact `PARTIAL` | `ARTIFACT_PARTIAL` |
| any artifact `SKIPPED` | `ARTIFACT_SKIPPED` |
| `events_truncated` | `EVENT_LIMIT_REACHED` |
| `frames_truncated` | `FRAME_LIMIT_REACHED` |
| `issues_truncated` | `ISSUE_LIMIT_REACHED` |
| `text_truncated` | `TEXT_TRUNCATED` |

The tuple is deduplicated and sorted lexicographically by
`RuntimeIncompleteReason.value`. Duplicate artifact collapse is not an
incomplete reason.

### 8.3 Counters and invariants

Before return, all of the following must hold:

```text
submitted_artifacts == len(artifacts) + duplicate_artifacts
total_input_bytes == sum(a.size_bytes for a in artifacts)
total_events == len(events) == sum(a.event_count for a in artifacts)
total_frames == len(frames)
             == sum(a.frame_count for a in artifacts)
             == sum(len(e.frames) for e in events)
ordinary_issues = len(issues) - (1 if issues_truncated else 0)
ordinary_issues <= config.max_issues
len(issues) <= config.max_issues + 1
sum(a.issue_count for a in artifacts) == len(issues)
ingestion_complete == (incomplete_reasons == ())
```

The `+ 1` is reserved for the single `ISSUE_LIMIT_REACHED` sentinel. Ordinary
issues are bounded by `max_issues`; the sentinel exists exactly when
`issues_truncated` is true, and there is at most one sentinel.

Additionally:

- Every event's `artifact_id` exists; every event sequence is contiguous from
  zero within its artifact.
- Every frame's `event_id` exists; frame ordinals are contiguous from zero
  within their event and match the frame's position in that event.
- Every issue's artifact exists.
- Every candidate node ID was validated against the supplied graph by the
  ingestor and is unique and sorted in the inventory. The inventory has no
  graph reference, so `__post_init__` can validate uniqueness, sorting, and
  primary containment but not graph membership.
- `primary_node_id`, when present, is in `candidate_node_ids`.
- `source_reference_sha256` is present exactly when `relative_path` is not
  `None`, and the path-match basis is consistent with path presence and
  verification status as defined in §10.
- Association and candidate shape is consistent with §10.3:
  `NOT_EVALUATED` and `UNASSOCIATED` have no candidates or primary; `FILE` has
  the file node; `SYMBOL` has the file and one symbol with the symbol primary;
  `AMBIGUOUS_SYMBOL` has the file and all containing symbols with no primary.
- `frames_by_node` indexes only the `primary_node_id` of `FILE` and `SYMBOL`
  associations; a symbol-associated frame appears under its primary symbol, not
  its FILE candidate (use `frames_by_file` for the file). Ambiguous and
  unassociated frames are not placed under a node.
- Index values exactly match a filter of the authoritative tuples; no index
  invents, reorders, or duplicates records.

### 8.4 Indexes

- `events_by_artifact`: every artifact ID, including empty tuples; values in
  that artifact's event order.
- `events_by_trace_id`: every non-null `trace_id`; values in global
  authoritative order (artifact order, then sequence).
- `events_by_fingerprint`: every fingerprint; values in global authoritative
  order, including repeated occurrences.
- `frames_by_file`: every non-null `relative_path`, whether graph-verified or
  retained as an unverified relative candidate, including unmatched and
  ambiguous relative-suffix paths; values in global authoritative order (event
  order, then ordinal).
- `frames_by_node`: every graph node ID that is the `primary_node_id` of at
  least one frame; values in global authoritative order. The mapping may omit
  graph nodes with no frames.

Backing dictionaries are copied before `MappingProxyType` exposure. Callers
cannot mutate a mapping, a tuple, or any retained backing structure.

## 9. Input formats

### 9.1 Common decoding rules

- Artifact content is exact bytes. No gzip, zip, archive, protobuf, pickle, or
  other decompression/deserialization is supported.
- Text is strict UTF-8 with at most one leading UTF-8 BOM. UTF-16/UTF-32,
  lossy decoding, and `surrogateescape` are rejected. Every decoded string that
  is retained, validated, or used in an identity must contain only Unicode
  scalar values: a valid JSON surrogate pair is decoded to the single scalar it
  encodes, while an unpaired high or low surrogate is invalid and is never
  encoded with `surrogatepass` or replacement characters.
- For line-oriented formats, raw bytes are split on LF before UTF-8 decoding,
  so one malformed line is recoverable without invalidating the rest of the
  artifact. One trailing CR is removed from each line as part of its
  terminator; a lone CR elsewhere is retained as text. A leading UTF-8 BOM is
  removed only from the first line at the start of the artifact.
  `JSON_EVENTS` document mode decodes the BOM-stripped document as one UTF-8
  value.
- Blankness is determined on the pre-decode line bytes after trailing-CR and
  first-line-BOM removal: a blank line is empty or contains only ASCII space and
  tab characters. It is skipped and does not create an event. Other whitespace
  is content, not blankness. Blank lines still increment the physical source
  line counter, and a blank line never produces `LINE_TOO_LONG`.
- For line-oriented formats, line length is measured on those same pre-decode
  bytes before UTF-8 decoding. A nonblank line whose length exceeds
  `max_line_bytes` is skipped with `LINE_TOO_LONG` without decoding; parsing
  continues with the next line. `JSON_EVENTS` document mode is not line-limited
  because the whole document is one value.
- A NUL in a line or retained string is invalid and reported. A line-oriented
  line containing NUL is skipped. JSON field-specific NUL handling is defined
  in §9.3. Unknown JSON keys are ignored and are not scanned for NUL.
- No Unicode normalization, case folding, whitespace trimming beyond the
  documented line terminator, or newline rewriting occurs.
- Retained text is bounded by UTF-8 byte length and truncated only at a valid
  code-point boundary with a final Unicode ellipsis. Truncation reserves three
  bytes for the ellipsis; if the configured bound cannot hold the ellipsis and
  at least one code point, the retained field is empty. Truncation is always
  reported, never silent.
- Control characters other than NUL are retained as data. Module 8 never
  renders them to a terminal, log stream, HTML, or LLM prompt. Consumers must
  treat every message and tag as untrusted text.

### 9.2 `TEXT_LOG`

One nonblank physical line becomes one `RuntimeEvent` with:

```text
kind = LOG
message = the exact line text (bounded)
source_line = one-based physical line
sequence = next zero-based event ordinal
```

V1 does **not** guess timestamp, level, logger, exception type, or stack
frames from arbitrary text. A line that resembles a traceback is still a log
message. This avoids format guessing and false structure. A producer that
wants structured fields must emit `JSON_EVENTS`; a producer that wants parsed
frames must submit `STACK_TRACE_TEXT` or JSON explicit frames.

A line skipped for invalid UTF-8, NUL, or overlong length is reported and
makes the artifact `PARTIAL` when at least one event was produced. An artifact
with no usable lines because every nonblank line was skipped is `FAILED`; an
empty or blank-only artifact is `COMPLETE` with zero events.

### 9.3 `JSON_EVENTS`

`JSON_EVENTS` supports two shapes, selected deterministically:

1. **Document mode:** the BOM-stripped content is one JSON value that is an
   object or array. A top-level object produces one event; a top-level array
   produces one event per element in array order. Array elements are expected to
   be objects; a non-object element is an invalid event. `source_line` is `None`
   for every document-mode event.
2. **JSONL mode:** each nonblank physical line is one JSON object. Events
   retain their one-based physical `source_line` and line order.

Selection rules:

- If the whole BOM-stripped content is one syntactically valid JSON value and
  that value is an object or array, document mode is used. A valid single-line
  object is therefore treated as a document, not as a one-line JSONL artifact;
  every document-mode event has `source_line=None`. V1 has no explicit document/
  JSONL mode selector and does not promise line-level provenance for a lone
  JSON object. A caller that needs line provenance for multiple events must
  submit them as JSONL, one object per nonblank line; a lone object cannot be
  given line provenance without a future API change. This is an explicit V1
  limitation, not a hidden mode.
- Otherwise, if at least one nonblank line independently has JSON object syntax,
  JSONL mode is used. Malformed lines are skipped with `MALFORMED_JSON`;
  syntactically valid scalar, array, or other non-object lines are skipped with
  `INVALID_EVENT_SCHEMA`. Either makes the artifact `PARTIAL` when another
  usable line produces an event.
- If no mode yields a usable event, the artifact is `FAILED`. Invalid UTF-8 is
  an `INVALID_UTF8` line failure in JSONL mode; a syntactically valid scalar or
  other valid non-object value is `INVALID_EVENT_SCHEMA`; otherwise
  `MALFORMED_JSON`. Document mode is selected only after document-level UTF-8
  validity, so invalid UTF-8 cannot select it.
- Empty or blank-only content is the empty JSONL shape and is valid with zero
  events. An empty array is also valid with zero events.

A non-object element in a document array is an invalid event and is reported;
the other elements may produce events. If no usable event remains, the artifact
is `FAILED`. A top-level array in JSONL mode is an invalid line, not an event.

Mode classification is separate from budget enforcement. Before `json.loads`
is invoked, an iterative, non-materializing structural JSON validator over the
BOM-stripped bytes validates complete JSON syntax and raw UTF-8 byte validity,
counts nodes and number-token bytes, and determines whether the content is
exactly one top-level object or array followed only by JSON whitespace. It
validates strings and escapes, commas, colons, literals, numbers, and nesting;
delimiter balance alone is not sufficient. Only then is document mode selected.
If the content is not exactly one syntactically valid object or array, JSONL
selection applies. A balanced but syntactically invalid or invalid-UTF-8
whole-content candidate therefore falls back to per-line JSONL recovery rather
than failing the artifact as a document.

A node is each top-level or nested object or array, each object-key occurrence
(including a duplicate key), and each scalar value (string, number, `true`,
`false`, or `null`). Punctuation is not a node. A number token is measured from
its optional leading `-` through its exponent, including `.`, `e`/`E`, and an
exponent sign, excluding surrounding whitespace.

Once document mode is selected, the same validator enforces `max_json_nodes` and
`max_json_number_bytes` before `json.loads` materializes values; an over-budget
document is `FAILED` with `MALFORMED_JSON`. In JSONL mode, each nonblank line
first receives the line-oriented checks used for `TEXT_LOG`: `max_line_bytes`
and a raw-NUL check on the physical line. Length is checked before raw NUL; a
nonblank overlong line is skipped with `LINE_TOO_LONG` even if it also contains
a NUL. An overlong whitespace-only line remains blank and is ignored without an
issue. A within-length raw-NUL line is skipped with `NUL_BYTE`. The line is then
strictly UTF-8 decoded, JSON-syntax validated and budgeted. An over-budget or
syntactically invalid line is skipped with `MALFORMED_JSON`, and later lines
continue. Aggregate node counts across JSONL lines are not compared to
`max_json_nodes`. The validator is linear, iterative, uses no dynamic regular
expression, and allocates no value-sized tree.

Failure classification at the selected boundary is ordered: for JSONL, a
nonblank overlong line is `LINE_TOO_LONG`, a raw-NUL line is `NUL_BYTE`, and
strict UTF-8 failure is `INVALID_UTF8` before JSON validation. Document mode is
reachable only after UTF-8 validity, so it has no separate document-level UTF-8
failure. After those checks, an over-budget value or a JSON-syntax failure is
`MALFORMED_JSON`; otherwise a duplicate object key is `DUPLICATE_JSON_KEY`;
otherwise a parser resource limit is `MALFORMED_JSON`; otherwise valid JSON of
the wrong shape or field type is `INVALID_EVENT_SCHEMA`.

A duplicate key anywhere in a document-mode value fails the whole document;
there is no per-array-element recovery. In JSONL mode it fails only the affected
line.

Parsing uses the standard-library JSON decoder with all of these safeguards:

- duplicate object keys are rejected with `DUPLICATE_JSON_KEY`;
- `NaN`, `Infinity`, and `-Infinity` are rejected;
- no custom object constructors, hooks that instantiate application types, or
  code execution are used;
- recursion/decoder depth failures are caught as malformed input, never
  allowed to crash the ingestor;
- numbers are not accepted where the schema requires a string or integer.

Each event object uses this schema. Unknown keys are ignored and not retained.
A key with the wrong type or an over-limit non-truncatable value makes that
event invalid and skipped with `INVALID_EVENT_SCHEMA`.

| Key | Type | Required | Bound / rule |
|---|---|---|---|
| `kind` | string enum | yes | `log`, `error`, or `span` |
| `message` | string | for `log` | truncated to `max_message_bytes` |
| `timestamp` | string | no | max 128 UTF-8 bytes; opaque |
| `level` | string | no | max 32 bytes; opaque, no normalization |
| `logger` | string | no | max 256 bytes |
| `operation` | string | for `span` | max 256 bytes |
| `trace_id` | string | no | max 256 bytes; opaque |
| `span_id` | string | no | max 256 bytes; opaque |
| `parent_span_id` | string | no | max 256 bytes; opaque |
| `duration_ms` | string | no | max 64 bytes; `^(0|[1-9][0-9]*)(\.[0-9]+)?$` |
| `status` | string | no | max 32 bytes; opaque |
| `tags` | object of strings | no | at most `max_tags`; key/value bounds; no nested values |
| `error` | object | for `error` when no message | only valid for `kind=error` |

`error` schema:

| Key | Type | Required | Bound / rule |
|---|---|---|---|
| `type` | string | no | max 256 bytes |
| `message` | string | no | truncated to `max_error_message_bytes` |
| `stack` | string | no | max `max_stack_bytes`; parsed only with a known language; over-limit or unparseable stacks retain the event without frames and make the artifact `PARTIAL` |
| `frames` | array of frame objects | no | at most `max_frames_per_event`; non-object elements invalidate the event |

Explicit frame object:

| Key | Type | Required | Rule |
|---|---|---|---|
| `function` | string | no | truncated to `max_frame_function_bytes` |
| `source` | string | no | parsed by the common source-reference rules; an over-limit, malformed, or NUL source retains the frame with no path and reports `INVALID_SOURCE_LOCATION`; a non-string invalidates the event |
| `line` | integer | no | one-based; values below 1 become `None` with `INVALID_SOURCE_LOCATION`; a boolean or non-integer invalidates the event |
| `column` | integer | no | exact as reported; negative values become `None` with `INVALID_SOURCE_LOCATION`; a boolean or non-integer invalidates the event |

Field mapping: `timestamp` becomes `timestamp_text`, `duration_ms` becomes
`duration_ms_text`, `error.type` becomes `error_type`, and `error.message`
becomes `error_message`; `tags` becomes `RuntimeTag` values. Explicit frames and
parsed `error.stack` frames become `RuntimeStackFrame` values. Explicit frames
use the artifact's declared language when it is known and `UNKNOWN` otherwise;
parsed stack frames are emitted only when the declared language is known.

Event validity:

- `kind=log` requires `message`.
- `kind=error` requires `message` or `error`; an error object on another kind
  is invalid.
- `kind=span` requires `operation`.
- `error.frames` takes precedence over `error.stack` when both are present;
  the stack text is ignored rather than parsed twice. An explicitly present
  empty `frames` list still suppresses stack parsing.
- For `error.stack`, language eligibility is checked before size and parsing.
  When `error.frames` is absent and `error.stack` is present without a known
  `declared_language`, the event is retained, no frames are parsed, and the
  artifact becomes `PARTIAL` with `STACK_LANGUAGE_MISSING`. The stack is not
  size-checked or parsed, so `STACK_TOO_LARGE` and `TEXT_TRUNCATED` are not
  additionally reported for that stack.
- When `error.frames` is absent, a known language is present, and
  `error.stack` exceeds `max_stack_bytes`, the event is retained without
  parsed frames, the event and artifact are marked `frames_truncated` and
  `text_truncated`, the artifact is `PARTIAL`, and `STACK_TOO_LARGE` and
  `TEXT_TRUNCATED` are reported.
- When `error.frames` is absent, a known language is present, and
  `error.stack` fits but yields no recognized frame, the event is retained, the
  artifact is `PARTIAL`, and `STACK_UNPARSEABLE` is reported. A NUL in an
  otherwise eligible, within-cap recognized stack string makes it unparseable
  under this rule; an over-cap stack follows the size rule above.
- A NUL in any other event-level retained string or in a frame `function`
  invalidates the event. A NUL in a frame `source` is handled as an invalid
  source reference, as above.
- An unpaired surrogate in a decoded event field invalidates the event with
  `INVALID_EVENT_SCHEMA`, except that a frame `source` is handled as an invalid
  source under §10.1, and an unpaired surrogate in an otherwise eligible
  `error.stack` makes that stack unparseable under the stack rule. A valid
  surrogate pair is one Unicode scalar and is valid. Unknown keys and their
  values are ignored and are not inspected for surrogates.
- Explicit frames beyond `max_frames_per_event` are omitted, the event and
  artifact are marked `frames_truncated`, and `FRAME_LIMIT_REACHED` is
  reported.
- A frame with an invalid source, line, or column is retained with the invalid
  component set to `None`; `INVALID_SOURCE_LOCATION` is reported and the
  artifact is `PARTIAL`. This does not by itself invalidate the event.
- Tags are sorted by key. Duplicate keys are impossible after JSON duplicate
  detection. Over-limit tags invalidate the event rather than being truncated.
- `message`, `error.message`, and truncated frame `function` text set the
  event's and artifact's `text_truncated` and report `TEXT_TRUNCATED`; other
  over-limit strings invalidate the event rather than being truncated.
- An event invalid for schema reasons is skipped and reported. If no usable
  event remains in the artifact, the artifact is `FAILED`; otherwise it is
  `PARTIAL`.

### 9.4 `STACK_TRACE_TEXT`

This format is explicitly one or more stack traces separated by blank lines.
It requires a known `declared_language` (`PYTHON`, `JAVA`, `JAVASCRIPT`,
`TYPESCRIPT`, or `TSX`); missing or `UNKNOWN` is a fatal validation error,
because the format cannot be parsed without knowing its grammar.

Each nonblank block is evaluated as one event, subject to the over-cap and
no-usable-line rules below:

```text
kind = ERROR
message = non-frame lines joined with LF (bounded), or None when there are none
source_line = one-based first line of the block
frames = parsed frames in reported order
```

V1 treats a block as one error event. `Caused by:`, `Suppressed:`,
`During handling of the above exception`, and similar chained sections remain
part of the same event; no cause tree is reconstructed. Multiple independent
errors must be separated by a blank line.

Frame grammars:

| Language | Frame form |
|---|---|
| Python | `File "source", line N, in function` |
| Java | `at qualified.method(Source.java:N)` or `at qualified.method(Source.java)` |
| JavaScript/TypeScript/TSX | `at function (source:N:C)` or `at source:N:C` |

Parsing rules:

- Python code-snippet lines are ignored, not retained as message text.
  Message lines are the unindented header/exception lines.
- Java/JavaScript message lines are the lines that do not match a frame or a
  continuation such as `... 12 more`.
- Frame function text is bounded and may be `None` when the format omits it.
- A malformed source reference does not drop the frame; the frame is retained
  with no resolved path, `INVALID_SOURCE_LOCATION` is reported, and the
  artifact becomes `PARTIAL`.
- If a usable block has no recognized frame, the event is retained with
  `STACK_UNPARSEABLE` and the artifact becomes `PARTIAL`.
- Frames beyond `max_frames_per_event` are omitted with
  `FRAME_LIMIT_REACHED`; the event and artifact are marked
  `frames_truncated`.
- Block bytes are the exact bytes of its nonblank physical lines after the
  common line-terminator normalization, including LF separators between them
  and excluding the separating blank line. If a block exceeds
  `max_stack_bytes`, process only the longest prefix of complete lines whose
  measured bytes fit within the cap; no line beginning after the cap is
  processed. Retain one event for the block even when that prefix is empty
  (`message=None`, no frames), set `frames_truncated` and `text_truncated` on
  the event and artifact, report `STACK_TOO_LARGE` and `TEXT_TRUNCATED`, and
  mark the artifact `PARTIAL`. This over-cap rule takes precedence over the
  later no-usable-lines rule for that block.
- Invalid UTF-8, NUL, or overlong lines are skipped with issues. A block that
  does not exceed `max_stack_bytes` and has no usable physical line (every
  nonblank line was skipped) produces no event. A block with at least one usable
  line always produces an event; if it has no recognized frame it follows the
  `STACK_UNPARSEABLE` rule above. If every block produces no event, the artifact
  is `FAILED`; if at least one block produces an event, the artifact is
  `PARTIAL` when any evidence was skipped.

### 9.5 Deferred formats

V1 does not accept OpenTelemetry protobuf, OTLP/HTTP payloads, syslog,
journald, Windows Event Log, browser performance traces, pprof, JFR, async
profiler dumps, binary core dumps, or arbitrary vendor JSON. A producer must
map its data to `JSON_EVENTS` or `STACK_TRACE_TEXT`. This is an intentional
contract, not auto-detection.

## 10. Source references, path matching, and graph association

### 10.1 Source reference normalization

A frame's reported source string is untrusted. Module 8 applies this fixed
normalization before any graph lookup:

1. Strip surrounding ASCII whitespace. Reject empty strings, NUL, C0 controls,
   CR/LF, unpaired surrogates, and strings longer than
   `max_source_reference_bytes`.
2. Replace `\` with `/`. This is matching normalization only; no filesystem
   resolution occurs.
3. A leading `//` is a UNC/network path and is `EXTERNAL_OR_NON_FILE`.
4. A leading `/` is a local absolute path.
5. A match of `^[A-Za-z]:/` is a local absolute drive path. A match of
   `^[A-Za-z]:` without the slash is a drive-relative form, rejected as invalid
   and unmatched; it is not treated as a URI scheme.
6. Recognize a URI scheme from the start with
   `^[A-Za-z][A-Za-z0-9+.-]*:`, case-insensitively:
   - `file:` is a local path. Only empty-authority forms (`file:/...` and
     `file:///...`) are accepted. `file://host/...` is a network reference and
     becomes `EXTERNAL_OR_NON_FILE`. Percent-decode the path to bytes, decode
     strictly as UTF-8, replace `\` with `/` again, and validate components;
     invalid percent escapes, invalid UTF-8, NUL, or controls make the
     reference invalid and unmatched. Query and fragment markers are not
     interpreted and make the reference invalid. After decoding and backslash
     normalization, a path matching `^/[A-Za-z]:` loses exactly one leading `/`
     and is then classified as drive-absolute or drive-relative. Reclassify the
     decoded path through
     steps 3–8: a leading `//` is UNC/external, a leading `/` or a drive path is
     local absolute, and a drive-relative form is invalid. Never retain a
     drive, UNC, or absolute path from `file:`.
   - `webpack:` is a bundler path. Accept only the `webpack://` prefix; other
     `webpack:` forms are invalid and unmatched. Remove `webpack:` and exactly
     two leading `/`. If the remainder then starts with `//`, it is UNC and
     `EXTERNAL_OR_NON_FILE`. Otherwise, if it starts with `/`, remove exactly
     one more leading `/` before classification. Reclassify the result with the
     same UNC, drive-absolute, drive-relative, absolute, and relative rules; do
     not force it to be relative. Thus `webpack://C:/x` and `webpack:///C:/x`
     are local absolute drive candidates used only transiently for suffix
     matching, `webpack://C:foo` is invalid drive-relative, and
     `webpack:////server/share` is UNC/external. Strip leading `./` from a
     relative remainder and reject an empty remainder.
   - Any other scheme (`http:`, `https:`, `node:`, `chrome:`, custom schemes)
     is `EXTERNAL_OR_NON_FILE`.
7. For every absolute path, reject empty, `.`, and `..` components after the
   leading separator and drive prefix; do not resolve them. For a relative
   path, strip repeated leading `./`, then reject any empty, `.`, or `..`
   component. Do not resolve `..`; an attempted traversal is never followed.
8. A relative path with no `/` is basename-only. A relative path with at least
   two components is a relative candidate.
9. No case folding, Unicode normalization, or platform path resolution occurs.

Matching is case-sensitive. This is deliberate: graph paths are authoritative
and case-insensitive matching could conflate distinct files. A runtime path
with different case is unmatched rather than guessed.

With no graph:

- a relative candidate is retained as `relative_path` with
  `UNVERIFIED_RELATIVE_PATH`;
- a basename-only, absolute, or external reference retains no path and is
  `UNMATCHED` or `EXTERNAL_OR_NON_FILE`.

With a graph:

- exact relative path match -> `EXACT_RELATIVE_PATH`;
- otherwise a relative candidate is checked for a unique component-wise
  suffix match -> `UNIQUE_PATH_SUFFIX`; multiple suffix matches -> `AMBIGUOUS`
  with the normalized relative path retained as an unverified candidate,
  `association=UNASSOCIATED`, `primary_node_id=None`, and
  `candidate_node_ids=()`; no match -> `UNMATCHED` with the normalized relative
  path retained as an unverified candidate;
- a basename-only reference is matched only when exactly one graph path has
  that basename -> `UNIQUE_BASENAME`; multiple matches -> `AMBIGUOUS`; no
  match -> `UNMATCHED` with no path;
- a local absolute path is matched by unique component-wise suffix against
  graph paths -> `UNIQUE_PATH_SUFFIX`; multiple -> `AMBIGUOUS`; none ->
  `UNMATCHED` with no path;
- an external/non-file reference -> `EXTERNAL_OR_NON_FILE` with no path;
- a frame with no source, or with a source rejected by the normalization in
  this section, is `UNMATCHED` with no path. No source string is invented.

Suffix matching is component-wise and uses a basename bucket before comparing
components, so it never scans every graph file for every frame. A suffix match
must match whole components, never a character substring. All matching paths
are repository-relative POSIX paths.

Absolute local paths, drive letters, UNC paths, and raw source strings are
never stored in the inventory. They may be used transiently for matching and
are then discarded.

`source_reference_sha256` is present only when `relative_path` is present. Its
canonical payload is:

```text
["tracerag-runtime-source-reference-v1", relative_path]
```

It therefore does not expose an absolute path. It is absent when
`relative_path` is `None`: unmatched absolute references, ambiguous or
unmatched basenames, ambiguous absolute-suffix references, external/non-file
references, invalid or missing sources, and frames with no source. It is
present for unmatched relative candidates and ambiguous relative-suffix
candidates that retain an unverified path; those paths are claims, not graph
verification. Because the resolved relative path can depend on the graph for an
absolute reported path, this hash and the event fingerprint can change with the
graph; event/frame/artifact IDs cannot.

### 10.2 Line and column semantics

- Runtime frame `line` is one-based exactly as reported. A line less than 1 is
  invalid: it becomes `None`, `INVALID_SOURCE_LOCATION` is reported, and the
  frame may still associate at file level.
- Runtime frame `column` is retained exactly as reported and is optional. It is
  **never** compared to Module 3 byte columns and never used for association.
  V8-style columns, Java columns, and Python columns have different semantics
  even when they are numeric.
- Module 3 `SourceLocation.start_line` is one-based. Its `end_line` is the
  one-based line of the exclusive end point, with a zero-based end column.
  Therefore a runtime line `L` is inside a symbol when:

```text
start_line <= L <= effective_end_line
effective_end_line =
    end_line                if end_column > 0
    end_line - 1            if end_column == 0
```

If `effective_end_line < start_line`, the symbol covers no complete line and
does not match any runtime line. This rule is exact for symbols that end at
the start of a line and for symbols that end partway through a line.

- A frame with no line can associate only to a FILE node.
- A frame whose line is outside every symbol but whose path resolves to a known
  graph file associates to that FILE node.
- A frame whose path is unknown or not in the graph is `UNASSOCIATED`.

Module 8 does not receive file sizes or source text, so it cannot verify that a
reported line exists in the file. A FILE association means only that the path is
known to the graph; it is not a claim that the line is in range.

### 10.3 Optional graph association

The graph is a static, namespace-matched, unversioned snapshot. Module 8 builds
a private index:

- FILE nodes by exact `relative_path`;
- SYMBOL nodes by their containing FILE path;
- each symbol's validated line interval.

Graph validation is fail-closed:

- the value must be an exact `CodeGraphInventory`;
- its namespace must be a nonempty scalar-only string of at most
  `max_namespace_bytes` UTF-8 bytes equal to the request namespace;
- `nodes` must be an exact tuple of `GraphNode` values;
- node IDs must be nonempty, unique, scalar-only strings, and every node's
  `repository_namespace` must equal the request namespace;
- every node's `node_kind` must be exactly `FILE` or `SYMBOL`;
- every `relative_path` must be a nonempty, scalar-only canonical relative
  POSIX path with no absolute prefix, drive prefix, backslash, NUL, or
  empty/`.`/`..` component;
- every FILE node must have `location is None`, and file paths must be unique;
- every SYMBOL node must have a structurally valid `SourceLocation`, and its
  path must have exactly one FILE node;
- a `SourceLocation` must have exact integer fields,
  `0 <= start_byte <= end_byte`, positive `start_line` and `end_line`,
  `start_line <= end_line`, nonnegative columns, and, when the lines are equal,
  `start_column <= end_column`.

Location validation is structural: Module 8 checks the documented
`SourceLocation` fields without importing Module 3, so it adds no direct
Module 3 dependency. It validates shape only; it does not reopen source files
or verify byte offsets against a file length. Graph edges, unresolved
references, and the graph's public index mappings are not used for association
and are not revalidated by Module 8.

A violation raises `RuntimeEvidenceGraphError`; Module 8 does not repair or
partially trust a malformed graph.

Association result:

| Situation | Association | Candidates | Primary |
|---|---|---|---|
| no graph supplied | `NOT_EVALUATED` | empty | `None` |
| no resolved path / path not in graph | `UNASSOCIATED` | empty | `None` |
| path known, no line | `FILE` | file node | file node |
| path known, line in no symbol | `FILE` | file node | file node |
| path known, line in exactly one symbol | `SYMBOL` | file + symbol nodes, sorted | symbol node |
| path known, line in multiple symbols | `AMBIGUOUS_SYMBOL` | file + all containing symbol nodes, sorted | `None` |

Association is by line containment only. Module 8 never resolves a function
name, call target, module, package, or exception type to a graph node. It never
chooses an innermost symbol when containment is ambiguous; it reports all
candidates. This mirrors Module 6's rule that uncertainty must remain visible.

The association proves only that a reported frame line lies inside a static
graph node's line span. It does not prove that the runtime code equals the
graph source, that the symbol executed, or that the frame caused the error.
`GraphBinding.NAMESPACE_MATCHED_UNVERSIONED` records this limitation in the
inventory.

## 11. Ingestion algorithm

The ingestor performs these steps in order:

1. Validate configuration (already validated at construction).
2. Validate request types, namespace, source names, artifact contents,
   formats, declared languages, and request-level claims.
3. Enforce the submitted-artifact count and submitted-input-byte envelope before
   any content hashing or deduplication.
4. Deduplicate exact artifacts by `(format, declared_language, content_sha256)`;
   detect source-name conflicts.
5. Enforce the unique total-input-byte envelope after deduplication.
6. Validate the optional graph and build the private file/symbol index.
7. Sort unique artifacts into canonical order.
8. For each artifact:
   - if its byte length exceeds `max_artifact_bytes`, mark it `SKIPPED`
     with an `ARTIFACT_TOO_LARGE` issue and continue;
   - if the event budget is already exhausted, mark it `SKIPPED` without
     parsing and continue;
   - parse according to its format, with all line, text, JSON-node, event, and
     frame limits applied;
   - normalize source references, associate frames, and compute IDs and
     fingerprints;
   - determine artifact status and issue records;
   - update global counters and truncation flags.
9. Apply record budgets exactly as defined in §12. Event-budget exhaustion
   stops event production and skips later artifacts; frame-budget exhaustion
   stops frame production but does not skip later events or artifacts;
   issue-budget exhaustion stops diagnostic issue retention only. The canonical
   artifact order makes the stopping point deterministic.
10. Build canonical tuples and indexes, then validate all invariants.
11. Return the immutable inventory.

A fatal error never returns a partial inventory. A recoverable per-artifact
failure returns a complete inventory object with explicit `FAILED`, `PARTIAL`,
or `SKIPPED` records and incompleteness reasons.

Unexpected internal exceptions are not converted into evidence. They propagate
as a sanitized `RuntimeEvidenceIngestionError` with a fixed message and no
artifact content, raw path, or parser text. Known malformed-input exceptions
(`UnicodeDecodeError`, `json.JSONDecodeError`, numeric-conversion `ValueError`/
`OverflowError`, recursion-depth failure, and a bounded JSON pre-scan rejection)
are handled at the artifact or line boundary and become evidence issues.
`MemoryError` is not treated as evidence; if one escapes, it propagates as the
same sanitized ingestion error without a partial inventory.

## 12. Resource bounds and scaling

The request envelope is checked in two phases, before parsing:

- before content hashing or deduplication:
  `len(request.artifacts) <= max_artifacts`, counting duplicate submissions,
  and `sum(len(artifact.content) for every submission) <=
  max_submitted_input_bytes`;
- after deduplication:
  `sum(unique artifact byte lengths) <= max_total_input_bytes`.

Violating any check is a fatal `RuntimeEvidenceLimitError`. These checks precede
per-artifact skipping, so an artifact above `max_artifact_bytes` is a recoverable
`SKIPPED` artifact only when both envelope checks pass; otherwise the request is
fatal. The submitted-byte cap bounds hashing work even when many duplicate
submissions collapse to a small unique total.

The request and supplied graph namespace are bounded by `max_namespace_bytes`
before identity work. During parsing:

- `max_line_bytes` bounds each line-oriented line before decoding.
- `max_json_nodes` bounds every JSON document or JSONL line before it is
  materialized as Python values; `max_json_number_bytes` bounds each numeric
  token.
- `max_message_bytes` and `max_error_message_bytes` bound event text by UTF-8
  bytes, with code-point-safe truncation.
- `max_stack_bytes` bounds each stack source/block.
- `max_frame_function_bytes` bounds function text.
- `max_tags`, `max_tag_key_bytes`, and `max_tag_value_bytes` bound tags.
- `max_frames_per_event` bounds each event; excess frames are omitted and
  reported.
- `max_events`, `max_total_frames`, and `max_issues` bound the complete
  inventory; exceeding one or dropping evidence because of it sets the
  corresponding truncation flag and incomplete reason, as detailed below.

Record budgets are applied in canonical parse order. A truncation flag is set
only when evidence is actually dropped; merely reaching a limit exactly does
not create a loss.

**Event budget:**

- `max_events` bounds retained events.
- If the event budget is already exhausted when an artifact would be parsed,
  that artifact is `SKIPPED` without parsing, `events_truncated` is set, and
  `EVENT_LIMIT_REACHED` is recorded. The first artifact skipped for this reason
  receives one `EVENT_LIMIT_REACHED` issue.
- If an accepted event would exceed `max_events` while the current artifact is
  being parsed, it is not emitted. The current artifact is `PARTIAL` (it has at
  least one emitted event), `events_truncated` is set, and one
  `EVENT_LIMIT_REACHED` issue is recorded on the current artifact. All later
  artifacts are `SKIPPED`.
- If the budget is reached exactly at the end of the last parsed artifact and
  no later artifact exists, no event is lost and `events_truncated` remains
  false.
- The per-artifact byte check in §11 still applies before event-budget
  skipping; an oversized later artifact is `SKIPPED` with
  `ARTIFACT_TOO_LARGE` rather than being attributed to the event budget.
- An artifact skipped solely because the event budget is exhausted counts as
  dropped evidence even though the ingestor has not parsed it and cannot prove
  that it would have produced an event. This is the deliberate conservative
  case behind `events_truncated`; it does not claim a specific omitted event.

**Frame budget:**

- `max_total_frames` bounds retained frames.
- If a frame would exceed the budget, it is not retained. The affected event
  and artifact get `frames_truncated`, the artifact is `PARTIAL`, inventory
  `frames_truncated` is set, and one `FRAME_LIMIT_REACHED` issue is recorded on
  the first affected event. Later events and artifacts continue to be parsed;
  every later event whose frames are dropped is marked `frames_truncated` and
  its artifact is `PARTIAL`, without additional issues.
- `max_frames_per_event` applies before the global budget and sets the same
  flags; the first affected event receives `FRAME_LIMIT_REACHED`.
- Reaching `max_total_frames` exactly without dropping a frame does not set
  `frames_truncated`.

**Issue budget:**

- `max_issues` bounds ordinary retained issues. Issues are deduplicated before
  budget accounting, so a duplicate does not consume a slot.
- If another issue would exceed the limit, that issue is dropped,
  `issues_truncated` is set, and exactly one `ISSUE_LIMIT_REACHED` sentinel is
  retained in addition. The sentinel points to the first artifact whose issue
  was dropped and copies that issue's `event_sequence` and `source_line`, or
  uses `None` when the dropped issue had none.
- Ordinary issues are at most `max_issues`; `len(issues) <= max_issues + 1`.
- Dropping diagnostic issues does not change artifact status, which is computed
  from parsing outcomes.

Text truncation is always local and reported; it sets the affected event's and
artifact's `text_truncated`, records `TEXT_TRUNCATED`, and never silently
discards the entire event.

Complexity targets:

```text
O(total accepted bytes + emitted events + emitted frames + graph index + matched paths)
```

Artifact parsing is linear in bytes plus emitted records. Graph indexing is
linear in graph nodes. Path suffix lookup uses basename buckets and
component-wise comparison, not an all-graph scan per frame. Frame association
sorts frames by line per file and sweeps symbol intervals; it does not perform
all-pairs symbol/frame comparison. Sorting the final tuples is
`O(n log n)` in the output size.

Memory is bounded by the largest accepted artifact, the largest line/stack
block, the JSON node budget, the graph index, and the emitted inventory. Raw
artifact bytes are not copied into the inventory; parsed temporary buffers are
released after each artifact. There is no wall-clock budget, background worker, thread pool, or
asynchronous queue.

## 13. Failure semantics

### 13.1 Fatal failures

The public exception hierarchy is:

```python
class RuntimeEvidenceError(Exception): ...
class RuntimeEvidenceConfigurationError(RuntimeEvidenceError): ...
class RuntimeEvidenceValidationError(RuntimeEvidenceError): ...
class RuntimeEvidenceLimitError(RuntimeEvidenceError): ...
class RuntimeEvidenceGraphError(RuntimeEvidenceError): ...
class RuntimeEvidenceInvariantError(RuntimeEvidenceError): ...
class RuntimeEvidenceIngestionError(RuntimeEvidenceError): ...
```

`RuntimeEvidenceInvariantError` is for direct public-model construction that
violates a local or cross-model invariant. The ingestor never exposes it as a
recoverable artifact outcome; an unexpected internal invariant failure is a
sanitized `RuntimeEvidenceIngestionError`.

| Condition | Exception |
|---|---|
| invalid configuration value or cross-field relation | `RuntimeEvidenceConfigurationError` |
| wrong request/artifact type, invalid or over-limit namespace, invalid source name, invalid claim, surrogate code point in a request string, source-name conflict | `RuntimeEvidenceValidationError` |
| `STACK_TRACE_TEXT` without a known declared language | `RuntimeEvidenceValidationError` |
| submitted artifact count, submitted input bytes, or unique total input bytes exceed the envelope | `RuntimeEvidenceLimitError` |
| graph wrong type, over-limit namespace, namespace mismatch, or malformed graph structure | `RuntimeEvidenceGraphError` |
| unexpected internal parser failure | `RuntimeEvidenceIngestionError` |

Fatal messages are fixed and sanitized. They never include artifact content,
message text, tags, raw stack text, absolute paths, or raw decoder/parser
exceptions. No partial inventory is returned.

### 13.2 Recoverable per-artifact outcomes

| Condition | Artifact outcome |
|---|---|
| empty or blank-only `TEXT_LOG`; empty or blank-only `STACK_TRACE_TEXT` with a valid declared language; empty/blank-only JSONL | `COMPLETE`, zero events |
| empty JSON array | `COMPLETE`, zero events |
| malformed lines mixed with usable events | `PARTIAL` plus issues |
| invalid-UTF-8 whole-content candidate with a usable object line | JSONL `PARTIAL`; `INVALID_UTF8` on the invalid physical line |
| invalid UTF-8 with no usable object line | `FAILED`, zero events, `INVALID_UTF8` line issues |
| JSON document, or an individual JSONL line, exceeds the structural node/number budget | `MALFORMED_JSON`; document `FAILED`, over-budget JSONL line skipped and artifact `PARTIAL` if another event remains |
| every nonblank line malformed or no usable event | `FAILED`, zero events |
| top-level scalar JSON value, or malformed document with no JSONL objects | `FAILED`, zero events |
| valid JSON with some invalid events | `PARTIAL` |
| valid JSON with all events invalid | `FAILED`, zero events |
| usable stack block with no recognized frames | event retained, artifact `PARTIAL` |
| text/stack/function truncation | affected event/artifact `PARTIAL` |
| issue-limit diagnostic drop only | artifact status unchanged; `issues_truncated` set |
| per-artifact byte limit exceeded, both request envelopes pass | `SKIPPED`, zero events, `ARTIFACT_TOO_LARGE` issue |
| event budget already exhausted before parsing | `SKIPPED`; no events |
| event budget exceeded while parsing | current artifact `PARTIAL`; later artifacts `SKIPPED` |
| frame budget drops frames | affected events/artifacts `PARTIAL`; later events still parsed |
| explicit frame list exceeds per-event limit | event retained, frames truncated, `PARTIAL` |

A `FAILED` artifact still appears in `artifacts` with its content hash, size,
source names, and issue records. It produces no events. Its failure does not
abort other artifacts. A `SKIPPED` artifact likewise appears with content hash,
size, and source names, but has zero events and no parsing outcome.

### 13.3 Cleanup

Module 8 has no external resource to clean up. It opens no file, starts no
process, creates no temporary file or directory, opens no network connection,
and holds no global registry. Temporary lists, maps, and decoded line buffers
are local to one call and are released on return or exception. The caller owns
the artifact bytes and any collection or storage policy around them.

## 14. Security and privacy

### 14.1 Trust boundaries

- Artifact bytes are untrusted data from an external producer.
- Artifact source names and request-level claims are untrusted metadata.
- The graph is trusted application data produced by Module 6, but Module 8
  still validates its public structure and namespace before use.
- Configuration and repository namespace are trusted application inputs.
- No artifact content can select a parser, module, executable, path, network
  destination, regular expression, or configuration value. Formats and
  languages are closed enums.

### 14.2 Execution and sandbox boundary

Module 8 must never:

- execute, import, evaluate, compile, or deserialize repository or artifact
  code;
- start a subprocess, shell, debugger, profiler, tracer, container, VM, or
  package manager;
- attach to or inspect a running process, memory, file descriptor, or browser;
- read or write repository files, environment variables, credentials, or
  configuration;
- access a network, Git, GitHub, an LLM, a database, or a vector store;
- install a dependency, load a plugin, or fetch a grammar.

It therefore provides no sandbox and makes no sandbox claim. The executable
boundary is not crossed. If controlled execution or instrumentation is ever
proposed, it requires a separate design with explicit isolation, credential,
network, filesystem, process-limit, artifact-capture, and cleanup guarantees.
It cannot be enabled by a Module 8 configuration flag.

### 14.3 Parser hardening

- Use the standard-library JSON decoder without application object hooks.
- Reject duplicate keys, `NaN`, infinities, recursion-depth failures, and
  unpaired surrogate escapes; valid surrogate pairs become one scalar.
- No `pickle`, `marshal`, `eval`, `exec`, `compile`, `importlib`, YAML
  constructors, XML entity resolution, or unsafe object loaders.
- No regular expression is constructed from artifact text. All frame grammars
  are fixed module constants with bounded input slices.
- No decompression, so compressed-input bombs are outside the contract.
- Every line, stack, event, frame, issue, and tag is structurally bounded.
  JSON documents and lines are structurally pre-scanned and node/number-bounded
  before value materialization.
- Control characters are retained as data but never rendered or logged.

### 14.4 Path safety

- Artifact text never causes a filesystem operation, so path traversal cannot
  escape anything.
- `..`, empty components, drive-relative paths, UNC paths, and absolute paths
  are never resolved or stored.
- Absolute paths are used only transiently for component-wise graph suffix
  matching and are then discarded.
- Graph association uses exact component matching, never string-prefix or
  substring matching.

### 14.5 Privacy and redaction

Runtime artifacts can contain secrets, personal data, source snippets,
credentials, tokens, and user content. Module 8 does **not** claim to detect or
redact secrets. Pattern-based redaction is intentionally not used in V1
because it is unreliable, can corrupt evidence, and can create false assurance.

The resolved privacy policy is:

- The caller must sanitize artifacts before ingestion when required by its
  policy. Module 8 treats submission as authorization to retain bounded
  evidence text.
- Raw artifact bytes are never retained in the inventory and never written to
  disk, a log, an exception, or a cache. Only the content hash, size, and
  parsed bounded fields remain.
- Every public record, request, and inventory follows the redacted
  representation rule in §5: `repr` may expose only stable IDs, enums, counts,
  booleans, and numeric positions, never free-form text, tags, function text,
  paths, source names, namespace, declared claims, trace/span IDs, graph/index
  contents, or artifact bytes.
- `retain_event_text=False` provides a metadata-only mode. It removes
  `message`, `error_type`, `error_message`, `logger`, `operation`, tags, and
  frame `function_text`. It retains structural enums, timestamps, levels,
  status, `trace_id`, `span_id`, `parent_span_id`, `duration_ms_text`, frame
  line/column, resolved relative paths, path-match basis, association, and IDs.
  Omitted text is not counted as text truncation; `text_truncated` and
  `TEXT_TRUNCATED` reflect ingestion truncation only. The inventory records
  `event_text_retained=False` so downstream consumers can distinguish policy
  omission from natural absence.
- Metadata-only projection is applied after all schema, type, required-field,
  NUL, tag count/type, and non-truncatable bound validation, but before
  text-field truncation and fingerprinting. Omitted free-text fields are
  neither truncated nor reported as `TEXT_TRUNCATED`, and their source content
  is not retained. Stack parsing still runs because frames are structural
  evidence; stack-size truncation remains an ingestion truncation.
- Even with `retain_event_text=False`, resolved relative paths and trace/span
  IDs may be sensitive. The caller still owns access control and downstream
  display policy.
- Messages and tags are never logged by Module 8. Normal logs contain only
  operation, counts, artifact IDs, statuses, issue codes, and sanitized
  outcomes.
- Exception messages are fixed templates. A raw decoder/parser exception is
  not chained into a public message.
- Source hashes and event fingerprints are not a promise of anonymity for
  low-entropy text. They are provenance/grouping aids, not cryptographic
  protection of the original text.

There is no credential input, credential storage, environment lookup, or
secret-scanning API in Module 8.

## 15. Integration with Modules 1–10

Dependency direction:

```text
Module 1 -> Module 2 -> Module 3
                \        /
                 v      v
                  Module 6
                     |
                     v
                 Module 8
```

Module 8 imports only the public Module 6 package. It does not import Modules
1–5, 7, 9, or 10. There is no cycle, and Module 6 has no dependency on Module
8.

- **Module 1:** The caller supplies the repository namespace. Module 8 does
  not parse a GitHub URL, clone, or read repository metadata.
- **Module 2/3:** Their relative paths and locations reach Module 8 only
  through the Module 6 graph. Module 8 does not rescan, parse, or reopen
  source.
- **Module 4/5:** No dependency. Module 8 does not chunk, embed, index, or
  search runtime text. A future Module 9 may choose to fuse runtime evidence
  with semantic results.
- **Module 6:** Optional static graph input. Module 8 copies node IDs and
  locations only for association; it never mutates the graph or invents a
  graph identity. The graph is namespace-matched but unversioned.
- **Module 7:** No dependency. A caller may supply a commit ID or revision as
  `declared_revision`, but Module 8 does not invoke Git, compare it to history,
  or treat it as proof. Module 7 facts remain separate.
- **Module 9:** May consume `RuntimeEvidenceInventory`, event/frame identities,
  fingerprints, trace IDs, and node indexes for fusion or ranking. Module 8
  defines no retrieval order, score, threshold, or ranking policy.
- **Module 10:** May cite event/frame evidence and graph candidates. Module 8
  makes no causal or answer claim; citation presentation is Module 10 work.

Forward compatibility is provided by immutable records, stable graph-independent
IDs, explicit provenance, a separate fingerprint, and optional association.
Modules 9–10 must not assume that a frame association proves execution or that
ingestion completeness proves capture completeness.

## 16. Logging

Module 8 uses `logging.getLogger(__name__)`.

- INFO: ingestion start and aggregate artifact/event/frame/issue counts.
- DEBUG: artifact ID, format, status, event count, frame count, and issue
  codes. V1 does not log source names or request-level claims; no configuration
  grants that.
- WARNING: recoverable artifact failure or truncation, identified by artifact
  ID and issue code.
- ERROR: fatal configuration, validation, graph, limit, or internal failure.

Logs never contain message text, tag values, stack text, function text, raw
artifact bytes, paths, source names, declared revision strings, exception
representations from parsers, or credentials. Logging is observational only
and never changes the returned inventory.

## 17. Testing strategy

Implementation will use strict pytest RED/GREEN TDD in a separately approved
implementation plan. All tests are offline, use synthetic bytes and temporary
graphs, and never require a real application, network, Git repository, LLM, or
vector store. Modules 1–7 behavior must remain green except for separately
tracked pre-existing failures, notably the Module 5 ChromaDB/Foyer
manifest-read defect; Module 8 must not modify, mask, or waive such failures.

### 17.1 Model and API tests

`tests/test_runtime_evidence_models.py`

- frozen/slotted behavior, field order, tuple and mapping immutability;
- stable enum values and public exports;
- configuration validation, including booleans rejected as integers,
  cross-field limits, namespace-byte, and JSON node/number limits;
- request/artifact validation, source-name rules, namespace rules, and
  rejection of all surrogate code points in request strings;
- direct model construction rejects surrogates in request/artifact fields,
  messages, tags, function text, paths, source names, claims, namespace, and
  index keys, using the input-model validation error and output-model invariant
  error respectively;
- redacted representations for every public record, request, and inventory:
  sentinel free text, tags, paths, source names, namespace, declared claims,
  trace/span IDs, graph/index contents, and bytes are absent; stable IDs, enums,
  counts, and numeric positions may remain;
- inventory `event_text_retained` reflects the effective configuration;
- direct issue construction rejects a message not matching its code;
- inventory counter/index invariants and `MappingProxyType` snapshots;
- convenience API delegation and separate-instance isolation.

### 17.2 Identity and deduplication tests

`tests/test_runtime_evidence_identities.py`

- exact canonical JSON payloads and pinned expected digests for artifact,
  event, and frame IDs, event fingerprints, and source-reference hashes;
- Unicode preservation, valid surrogate-pair decoding, null optionals, tag
  ordering, and frame ordering;
- identical artifact content under different source names collapses with all
  distinct names retained and the correct duplicate count;
- repeating one source name with the same deduplication key increases the
  duplicate count but not the source-name tuple;
- same source name with different content/format/language is a fatal conflict;
- repeated identical events remain distinct with distinct sequences/IDs;
- identical issues deduplicate by the documented key;
- event fingerprint grouping across artifacts without event removal, including
  repository namespace in the fingerprint payload;
- graph changes do not change artifact/event/frame IDs;
- a graph change that resolves an absolute source suffix differently changes
  the fingerprint and source hash but not artifact/event/frame IDs;
- `retain_event_text` changes fingerprints but not artifact/event/frame IDs;
- unpaired surrogates in JSON fields, frame sources, and stack text follow the
  documented invalid-event/unparseable-stack outcomes and never escape as
  `UnicodeEncodeError` or a hash failure;
- unresolved absolute/external source references are absent from identity and
  cannot distinguish otherwise-equal fingerprints;
- source-reference hashes are present exactly when a relative path is present,
  including an unmatched relative candidate;
- no separate absolute paths, source names, graph IDs, or Module 8-generated
  timestamps enter artifact/event/frame identity; timestamp bytes affect the
  artifact content hash only as artifact content.

### 17.3 Validation and limit tests

`tests/test_runtime_evidence_validation.py`

- wrong request/artifact/sequence types, exact-tuple requirements, and
  invalid enum values;
- empty, exactly-at-limit, and one-byte-over `max_namespace_bytes` namespaces
  with multibyte content: the exact limit is accepted and the over-limit value
  is rejected; invalid
  declared claims, including rejection of all surrogate code points in request
  strings, even a literal high-plus-low pair;
- a graph namespace exactly at the limit is accepted; one byte over is rejected
  as a graph error before any identity work;
- surrogate node IDs or relative paths in a supplied graph are rejected as
  graph errors;
- artifact count, submitted-byte envelope, and unique-total envelope failures,
  including proof that count/submitted checks occur before content hashing;
- per-artifact oversize becomes `SKIPPED` with `ARTIFACT_TOO_LARGE` only when
  both request envelopes pass; envelope failure takes precedence and is fatal;
- invalid config values and cross-field relations;
- graph type/namespace mismatch, duplicate file paths/node IDs, missing
  symbol locations, invalid intervals, and symbol paths without a FILE node;
- event-budget equality versus an actually dropped event; later artifacts are
  `SKIPPED` only when the event budget is exhausted;
- frame-budget exhaustion keeps later events and marks every affected event;
- issue-limit sentinel count, placement, and `len(issues) <= max_issues + 1`;
- no partial inventory after any fatal error.

### 17.4 Text adapter tests

`tests/test_runtime_evidence_text.py`

- LF, CRLF, blank lines, whitespace-only lines, and exact source line numbers;
- per-line UTF-8 decoding, UTF-8 BOM handling, strict rejection of UTF-16, and
  recovery from one invalid line;
- NUL, control characters, and overlong lines;
- overlong whitespace-only lines remain blank, do not produce `LINE_TOO_LONG`,
  and leave a blank-only artifact `COMPLETE`;
- empty, blank-only, all-invalid, and mixed valid/invalid artifacts;
- message truncation at a UTF-8 code-point boundary with an ellipsis;
- no timestamp/level/stack guessing from arbitrary text;
- large-line and event-budget boundaries.

### 17.5 JSON adapter tests

`tests/test_runtime_evidence_json.py`

- deterministic document-versus-JSONL selection, single-line object document
  behavior with `source_line=None` and no promise of line provenance for a lone
  object, document array, empty array, empty/blank JSONL, and top-level
  scalar/array rejection in JSONL;
- malformed whole document with and without a recoverable JSONL object line;
- balanced but syntactically invalid whole-content documents (invalid escape,
  missing comma, trailing comma) with a recoverable object line: each invalid
  form falls back to JSONL, reports `MALFORMED_JSON` for that line, and retains
  the valid object as an event; without a valid object line the artifact fails;
- duplicate keys (including a lone document object), `DUPLICATE_JSON_KEY`
  precedence, `NaN`/infinities, deep nesting/recursion failure, malformed JSON,
  and exact line/event recovery;
- syntactically valid scalar/array JSONL lines produce `INVALID_EVENT_SCHEMA`,
  not `MALFORMED_JSON`;
- full syntax-validating structural scan: large flat arrays and deeply nested
  unknown structures are rejected before full value materialization; braces
  inside strings are not counted; escaped quotes and backslashes are validated
  rather than treated as structure;
- exact node-count boundaries for `{}`, `{"a":1}`, `[{}]`, `{"a":{}}`,
  `[ "x" ]`, `{"a":true}`, `{"a":false}`, `[null]`, an escaped object key, and
  a duplicate-key occurrence; exact numeric-token boundaries including
  `-1.2e+3`; one node or one byte over budget is rejected;
- many individually valid JSONL lines whose aggregate nodes exceed
  `max_json_nodes` are accepted, because the budget is per line;
- an over-budget first JSONL line is skipped and a later valid line still
  produces an event; an over-budget document fails as a whole;
- malformed whole-content fallback to per-line JSONL recovery;
- a duplicate key in a document array fails the whole document, while a
  duplicate key in a JSONL line skips only that line;
- JSONL overlong and raw-NUL lines receive `LINE_TOO_LONG` and `NUL_BYTE`
  respectively before UTF-8/JSON classification; a line both overlong and
  NUL-containing yields `LINE_TOO_LONG`; an overlong whitespace-only line
  remains blank; a later valid object still produces an event;
- an invalid-UTF-8 whole-content candidate falls back to JSONL: a valid object
  line remains usable while the invalid physical line has `INVALID_UTF8` and
  its one-based `source_line`; a candidate with no usable object line fails;
- invalid UTF-8 precedes JSON-syntax/duplicate classification on a JSONL line;
- oversized numeric tokens, numeric-conversion `ValueError`/`OverflowError`,
  and parser resource-limit failures become recoverable `MALFORMED_JSON`
  outcomes rather than fatal ingestion errors;
- valid surrogate pairs and unpaired surrogate escapes in every retained JSON
  field, including the documented invalid-event, invalid-source, and
  unparseable-stack outcomes;
- every schema field and type, unknown fields ignored, absent optionals;
- log/error/span required-field rules and error-only-on-error;
- `duration_ms` decimal validation and exact text retention;
- tags bounds, sorting, string-only values, and metadata-only omission;
- explicit frames precedence over `stack`, frame bounds, invalid source/line/
  column retention, and non-object frame rejection;
- message/error-message/function truncation versus non-truncatable invalid
  fields;
- all-invalid versus some-invalid events, and event/frame/issue caps with
  deterministic partial outcomes.

### 17.6 Stack-trace tests

`tests/test_runtime_evidence_stack.py`

- Python, Java, JavaScript, TypeScript, and TSX frame grammars;
- `Caused by`, `Suppressed`, `... N more`, nested/chained blocks, and multiple
  trace blocks separated by blank lines;
- Python code-snippet lines ignored and header/exception lines retained;
- frames with no source, native/unknown sources, malformed locations, and
  columns preserved exactly;
- no recognized frames, missing language, unknown language, and oversized
  blocks, including language precedence over stack size, NUL stack text, and
  unpaired-surrogate stack text;
- over-cap blocks retain one event even when no complete prefix line fits;
- per-event frame caps and global frame caps, including `frames_truncated` and
  later-event status;
- a block with no usable lines, all blocks unusable, and mixed usable/unusable
  blocks;
- exact frame order and function-text truncation.

### 17.7 Association tests

`tests/test_runtime_evidence_association.py`

- no graph -> `NOT_EVALUATED`;
- exact relative path, unverified relative path, unique suffix, multiple
  relative suffix matches, unique basename, ambiguous basename, unmatched
  relative, absolute local drive and POSIX paths, UNC, `file:`, `webpack:`, and
  external schemes;
- multiple relative suffix matches retain an unverified path and source hash
  but produce no association or primary node;
- drive letters are not mistaken for URI schemes; `file://host` is external;
  percent-decoded and backslash-normalized `file:` paths are validated;
  `file:///C:/...` is drive-absolute and `file:////server/share` is UNC/external,
  with neither retained as a relative path;
- `webpack://C:/...`, `webpack:///C:/...`, `webpack://C:foo`, and
  `webpack:////server/share` follow the reclassification rules and never leak a
  drive or UNC path as a retained relative path;
- case-sensitive matching and no substring/prefix false positives;
- `..`, `.`, empty components, drive-relative paths, NUL, control chars, query/
  fragment `file:` forms, and invalid percent escapes never resolve or leak;
- file-only association, one symbol, nested/multiple symbols, and ambiguous
  candidate sets;
- line containment at `start_line`, mid-symbol, `end_line` with positive
  `end_column`, `end_line` with zero `end_column`, and outside all symbols;
- columns never used for association;
- failed files still associate at file level;
- unmatched relative candidates and ambiguous relative-suffix candidates
  retain a path and source-reference hash, while unmatched absolute/basename/
  external references do not;
- no-source and invalid-source frames use `UNMATCHED` with no path or source
  hash;
- `frames_by_file` indexes unverified and ambiguous relative-suffix candidates
  as well as graph-verified paths;
- graph namespace mismatch and malformed graph fail closed;
- candidate IDs exist, are unique, sorted, and `primary_node_id` is contained;
- `frames_by_node` excludes ambiguous/unassociated frames.

### 17.8 Determinism and ordering tests

`tests/test_runtime_evidence_determinism.py`

- shuffled artifact input produces identical artifacts/events/frames/issues,
  IDs, statuses, counters, and indexes;
- source-name case/Unicode ordering is deterministic;
- timestamps do not reorder events;
- JSONL/document event order is preserved;
- repeated runs are byte-for-byte equal under canonical model comparison;
- fingerprints group repeated content without deleting occurrences;
- issues, including the issue-limit sentinel, have documented order, source
  lines, and deduplication;
- incomplete reasons are deduplicated and sorted lexicographically by value;
- index keys and values have documented order and immutability.

### 17.9 Security and privacy tests

`tests/test_runtime_evidence_security.py`

- patch `open`, `Path.read_text`, `Path.read_bytes`, `os.scandir`,
  `subprocess`, network entry points, `eval`, `exec`, `compile`,
  `importlib.import_module`, and unsafe deserializers to fail if called;
- assert no filesystem, subprocess, network, Git, database, or LLM access;
- assert raw bytes are absent from inventory, every public `repr`, logs, and
  exceptions; sentinel messages, tags, function text, paths, source names,
  namespace, declared claims, and trace/span IDs are absent from every public
  `repr`;
- assert messages/tags/function text are absent from normal logs;
- unpaired surrogates and valid surrogate pairs never escape as an encoding
  failure, internal exception, or malformed canonical hash;
- metadata-only mode omits exactly the documented fields, retains
  association/provenance, sets inventory `event_text_retained=False`, and does
  not set `text_truncated` merely because text was removed;
- metadata-only projection still applies schema/NUL validation, still parses
  structural stack frames, and performs projection before text truncation and
  fingerprinting;
- hostile messages, ANSI escapes, bidirectional controls, and NUL are inert;
- JSON bombs, duplicate-key objects, huge tags, and deep nesting are bounded;
- no temporary file, cache, global registry, or open resource is created;
- exception messages are fixed and content-free.

### 17.10 Integration tests

`tests/test_runtime_evidence_integration.py`

- real Module 2 scanner -> Module 3 parser -> Module 6 graph on a synthetic
  repository, with synthetic runtime artifacts;
- exact relative path and line association to a known symbol;
- file-only association for a failed or symbol-free file;
- namespace mismatch fails without modifying the graph;
- no source reread during ingestion (patch source-reading APIs);
- Module 8 output can be consumed without mutation or conversion by a future
  consumer.

### 17.11 Scaling tests

`tests/test_runtime_evidence_scaling.py`

- operation counts proportional to bytes/events/frames, not all-pairs;
- the JSON structural pre-scan is linear and rejects over-budget input before
  full value materialization;
- basename-bucket suffix lookup avoids full-graph scans per frame;
- frame association uses the per-file line sweep;
- event/frame/issue limits produce deterministic truncation flags, counters,
  and the single issue-limit sentinel;
- indexes and tuples remain immutable under attempted mutation;
- no retained raw artifact bytes after return (reference/field inspection).

### 17.12 Acceptance criteria for eventual implementation

Module 8 is implementation-complete only when:

1. Public models, enums, exports, and the two entry points match this design.
2. All three formats parse with the documented bounded, recoverable behavior,
   including the bounded JSON structural pre-scan and numeric-token limits.
3. Artifact/event/frame IDs, event fingerprints, and source-reference hashes
   match exact canonical payloads and pinned golden digests.
4. Artifact deduplication and occurrence-preserving event behavior are proven.
5. Source-reference normalization, including `webpack:` reclassification,
   never stores absolute/machine/UNC paths and never follows traversal
   components.
6. Graph association is line-only, candidate-complete, deterministic, and
   explicitly unversioned.
7. All budget, truncation, status, counter, ordering, and index invariants hold.
8. Fatal errors return no partial inventory; per-artifact failures do not abort
   the request.
9. Security tests prove no execution, filesystem, network, subprocess, Git,
   database, LLM, or unsafe deserialization access.
10. Privacy tests prove raw bytes, free-form text, tags, paths, source names,
    namespace, declared claims, and trace/span IDs never leak through logs,
    exceptions, or any public `repr`; metadata-only mode behaves as specified.
11. Real Module 2 -> 3 -> 6 integration passes without upstream changes.
12. Module 1–7 suites and the full offline suite remain green except for any
    pre-existing Module 5 ChromaDB/Foyer manifest-read failure tracked
    separately; that defect is not waived, modified, or counted as a Module 8
    regression.
13. No Modules 9–10 functionality, no persistence, and no new dependency are
    introduced.
14. Independent review approves the implementation with no Critical or
    Important findings outstanding.
15. `compileall`, `pip check`, and `git diff --check` pass in the eventual
    implementation worktree.

These are future implementation gates, not claims that implementation exists
or that any suite was run for this documentation change.

## 18. Explicit non-goals

V1 does not implement:

- controlled execution, test running, builds, containers, debuggers, profilers,
  tracers, process attachment, or instrumentation;
- a sandbox, VM, container runtime, or execution policy;
- log shipping, file tailing, directory discovery, syslog/journald/Event Log
  collection, OTLP receivers, or network ingestion;
- gzip/zip/archive decompression, protobuf, pprof, JFR, core dumps, or binary
  trace formats;
- arbitrary vendor JSON auto-detection or a general schema-mapping language;
- explicit JSON document/JSONL mode selection; V1 selects by content shape and
  a lone JSON object has no line-level provenance;
- automatic secret scanning, redaction, anonymization, or privacy guarantees;
- global chronological ordering or timestamp parsing;
- causal analysis, incident reconstruction, impact scoring, severity ranking,
  anomaly detection, or root-cause inference;
- source reading, code parsing, graph construction, graph mutation, or
  revision binding;
- embeddings, vector search, BM25, hybrid retrieval, graph expansion, fusion,
  reranking, LLM prompting, or answer generation;
- Git history, commit verification, branch/worktree interpretation, or
  deployment metadata;
- persistence, SQLite/JSON storage, a database, a UI, an API server,
  authentication, or frontend behavior;
- Modules 9–10 behavior or any modification to Modules 1–7.

## 19. Resolved design decisions and remaining validation

Resolved:

- **Architecture:** ingestion-only, bytes-in; no execution, instrumentation, or
  sandbox claim.
- **Graph:** optional Module 6 snapshot, namespace-matched but unversioned;
  line-containment association only; ambiguous symbol candidates preserved;
  ambiguous relative-suffix paths retain an unverified path but no association.
- **Formats:** raw text lines, canonical JSON events, and explicit stack-trace
  text. JSON document versus JSONL is selected by content shape; there is no
  explicit mode selector, and a lone object has no line-level provenance. No
  vendor auto-detection or compression.
- **Unicode:** retained strings are Unicode scalar-only; valid surrogate pairs
  become one scalar, and unpaired surrogates are handled as malformed input,
  never with `surrogatepass`.
- **Identity:** content-addressed artifact IDs; occurrence-based event/frame
  IDs; separate fingerprint for grouping.
- **Deduplication:** exact artifact collapse only; event/frame occurrences are
  never content-deduplicated.
- **Ordering:** canonical artifact order plus per-artifact sequence; timestamps
  are opaque and never authoritative.
- **Completeness:** ingestion completeness is separate from the caller's
  capture-completeness claim; absence is not negative evidence.
- **Privacy:** no automatic redaction; caller sanitization; metadata-only mode
  recorded by `event_text_retained`; no raw bytes retained; redacted `repr` for
  every public record, request, and inventory; sanitized logs/exceptions.
- **Limits:** explicit submitted/unique byte, JSON node/number, line, event,
  frame, issue, and tag bounds with deterministic partial/skipped behavior.
- **Failures:** small fatal hierarchy; recoverable per-artifact outcomes; no
  partial inventory on fatal error.
- **Dependencies:** standard library only; Module 6 is the only direct
  project dependency.

Important implementation validation, not a design blocker:

- The V1 default budgets should be exercised against representative real log
  and stack-trace artifacts before release. If normal evidence is routinely
  truncated, the defaults need an explicit compatibility decision rather than
  silent expansion.
- The fixed stack grammars and source-reference rules should be validated
  against real Python, Java, and V8 traces from the supported project
  languages. A format that cannot be parsed safely remains retained as an
  event with an issue; it is never guessed.

No Critical unresolved design questions are known to the author; independent
review remains the gate for implementation planning.

## 20. Package layout and README changes

Recommended implementation layout:

```text
backend/runtime_evidence/
    __init__.py          explicit public exports
    models.py            frozen public values and enums
    config.py            RuntimeEvidenceConfig validation
    exceptions.py        public exception hierarchy
    identities.py        canonical IDs and fingerprints
    validation.py        request, artifact, and cross-model validation
    text_adapter.py      TEXT_LOG parsing
    json_adapter.py      JSON_EVENTS parsing and schema validation
    stack_parser.py      Python/Java/V8 stack grammars
    association.py       source normalization and Module 6 association
    ingestor.py          orchestration, budgets, statuses, counters
    indexes.py           immutable index construction

tests/
    test_runtime_evidence_models.py
    test_runtime_evidence_identities.py
    test_runtime_evidence_validation.py
    test_runtime_evidence_text.py
    test_runtime_evidence_json.py
    test_runtime_evidence_stack.py
    test_runtime_evidence_association.py
    test_runtime_evidence_determinism.py
    test_runtime_evidence_security.py
    test_runtime_evidence_integration.py
    test_runtime_evidence_scaling.py
```

Exact file names may change during implementation, but the public boundary and
the separation between models, parsing, association, orchestration, and
indexes are normative.

The implementation phase will append a Module 8 section to `README.md`
covering purpose, the bytes-in contract, formats and JSON schema, optional
graph association, evidence-versus-causality limits, privacy behavior,
resource limits, public API, examples, and explicit non-goals. It will not
rewrite Modules 1–7. No README change is made in this design-only phase.

## 21. Review verdict target

This specification is ready for implementation planning only if an independent
reviewer concludes both:

- **Architectural suitability: PASS** — ingestion-only is consistent with the
  project's security model, the Module 6 dependency is acyclic, and the
  evidence-versus-causality boundary is complete.
- **Specification precision and implementability: PASS** — models, formats,
  identities, ordering, association, budgets, failures, privacy, and tests are
  defined without contradictory or missing behavior.

Any Critical or Important finding must be resolved and the corrected
specification re-reviewed before an implementation plan is written.
