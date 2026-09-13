# Module 6 — Code Graph Builder

Date: 2026-09-13

Status: approved architecture recorded for written-specification review.
This document defines behavior and boundaries, not an implementation plan.

## Purpose and dependency position

Convert validated Module 2 `FileInventory` and Module 3 `CodeParseInventory`
metadata into a deterministic, immutable, in-memory `CodeGraphInventory`.
Precision takes precedence over apparent completeness. A resolved edge means
the available static metadata establishes exactly one internal relationship.
Uncertainty becomes explicit unresolved evidence; it never becomes a guess.
This is a static declaration/dependency graph, not a claim about runtime dispatch.

```text
Module 1 -> Module 2 -> Module 3
                            /       \
                           v         v
                       Module 4   Module 6 <- Module 2
                        Chunks    Code Graph
                           |         |
                           v         |
                       Module 5      |
                    Semantic Index   |
                            \       /
                             Module 9
                            Hybrid RAG
```

Module 6 depends only on Modules 2 and 3. The Module 4 -> Module 5 branch
and the Module 6 branch are independent inputs to the future Module 9.
There is no dependency on `CodeChunkInventory`, embeddings, or vector storage.
Mentioning downstream consumers here does not authorize Modules 7–10 work.

## Existing contracts and compatibility

The baseline inspected is commit `52e74a79d92ee947f6d246a18b959931c877cd0f`.
The authoritative public definitions are `backend/file_scanner/models.py`,
`backend/file_scanner/__init__.py`, `backend/code_parser/models.py`, and
`backend/code_parser/__init__.py`. Parser registry, orchestration, and language
extractors establish how those fields are populated.

- `FileInventory` contains `repository_path`, `total_files_seen`,
  `included_files`, `ignored_files`, `files`, `ignored`,
  `skipped_directories`, and `repository_namespace`.
- `ScannedFile` contains `relative_path`, `filename`, `extension`, `language`,
  `category`, and `size_bytes`. Only `FileCategory.SOURCE` and
  `FileCategory.TEST` are parser candidates.
- `CodeParseInventory` contains `repository_path`, `total_files_requested`,
  `success_files`, `partial_files`, `failed_files`, `skipped_files`, `files`,
  `skipped`, and `repository_namespace`.
- `ParsedFile` contains `relative_path`, `language`, `status`, `symbols`,
  `imports`, `calls`, `issues`, and optional `source_sha256`.
- `SymbolInfo` contains `name`, `kind`, `qualified_name`,
  `parent_qualified_name`, `location`, `parameters`, `return_type`,
  `modifiers`, `base_types`, and `implemented_types`.
- `ParameterInfo` contains `name`, `type_name`, and `default_value_text`.
- `ImportInfo` contains `module`, `bindings`, `is_wildcard`, `modifiers`,
  and `location`. `ImportBinding` contains `imported_name` and `alias`.
- `CallSite` contains `caller_qualified_name`, `callee_text`, `kind`, and
  `location`. `CallKind` has `CALL` and `CONSTRUCTOR`; there are no call
  argument types, argument counts, receiver bindings, or resolved target IDs.
- `SourceLocation` contains `start_byte`, `end_byte`, `start_line`,
  `start_column`, `end_line`, and `end_column`. Preserve these values;
  byte ranges are half-open original-file coordinates (including the BOM
  adjustment), lines are one-based, and columns are zero-based byte columns.
- `SymbolKind` has `CLASS`, `INTERFACE`, `FUNCTION`, `METHOD`, `CONSTRUCTOR`,
  `ENUM`, and `CONSTANT`. Nesting is a parent relationship, not another kind.
- `ParseStatus` has `SUCCESS`, `PARTIAL`, and `FAILED`.

Both inventory namespace fields are `str | None` upstream. Module 6 requires
equal, nonempty strings and rejects `None`; it never derives a namespace from
`repository_path`. This is an explicit graph input precondition, not an
upstream model change. Absolute repository paths are not graph identity data.

`ParsedLanguage` has `PYTHON`, `JAVA`, `JAVASCRIPT`, `TYPESCRIPT`, and `TSX`.
JSX uses `JAVASCRIPT`: scanner selectors are python/.py, java/.java,
javascript/.js or .jsx, and typescript/.ts or .tsx, the last mapping to `TSX`.
No `JSX` enum or new language taxonomy is introduced.

Java package information is embedded in symbol qualified names, not a
`ParsedFile.package` field. Python and ECMAScript qualified names are lexical
and need file context. Module lookup keys may be derived privately from paths;
stored symbol qualified names must remain exactly as supplied.

`base_types` and `implemented_types` are string tuples without per-target
locations. Each relationship uses its declaring `SymbolInfo.location` and
the exact type string. This is declaration-level evidence, not a fabricated
token location. Default/namespace import markers exist in ECMAScript bindings;
an export graph, re-export map, or alias configuration does not exist.

## Public API and ownership

```python
class CodeGraphBuilder:
    def build(
        self,
        file_inventory: FileInventory,
        parse_inventory: CodeParseInventory,
    ) -> CodeGraphInventory:
        ...

def build_code_graph(
    file_inventory: FileInventory,
    parse_inventory: CodeParseInventory,
) -> CodeGraphInventory:
    ...
```

The API has no filesystem path argument and no source-reading callback.
Each build owns its temporary state; previous builds cannot affect results.
Public exports include both entry points, the four graph models, four graph
enums, and the typed graph exception hierarchy.

Recommended package boundaries:

```text
backend/code_graph/
    __init__.py
    models.py
    exceptions.py
    identities.py
    validation.py
    indexes.py
    builder.py
    resolvers/
        __init__.py
        base.py
        python.py
        java.py
        ecmascript.py
```

Models own immutable contracts; identities own canonical hashing; validation
owns input and graph checks; indexes own reusable lookup structures; the
builder coordinates passes. Resolvers interpret validated metadata using
read-only indexes and return resolution outcomes. They do not mutate graph
state, choose tie winners, or access source. ECMAScript shares rules across
JS/JSX/TS/TSX, with language-specific eligibility checks.

## Graph values

Use TraceRAG-owned frozen, slotted dataclasses, immutable tuples, and read-only
mappings. Do not use NetworkX. Enum member values are lowercase spellings
of their names, including `incomplete_parse`.

`GraphNodeKind`: `FILE`, `SYMBOL`.

`GraphNode` fields:

- `node_id: str`, `node_kind: GraphNodeKind`, `repository_namespace: str`.
- `relative_path: str`, `language: ParsedLanguage`, `parse_status: ParseStatus`.
- `symbol_kind: SymbolKind | None`, `name: str | None`,
  `qualified_name: str | None`, `location: SourceLocation | None`.

Create one FILE per `ParsedFile`, including failed files. Its four optional
symbol fields are `None`; it is also the module dependency unit. No MODULE
node exists. Scanner-only and skipped parser files have no graph node.
Every validated stable `SymbolInfo` becomes a SYMBOL, copying `kind` into
`symbol_kind` and copying names/location unchanged. Language and parse status
come from its containing `ParsedFile`. Symbol optional fields are all present.
Imports and calls are evidence/relationships, never nodes.

`GraphEdgeKind`: `DEFINES`, `CONTAINS`, `IMPORTS`, `CALLS`, `INHERITS`,
`IMPLEMENTS`.

`GraphEdge` fields are `edge_id: str`, `edge_kind: GraphEdgeKind`,
`source_node_id: str`, `target_node_id: str`, and
`location: SourceLocation | None`.

- FILE `DEFINES` a top-level SYMBOL; SYMBOL `CONTAINS` a directly nested
  SYMBOL in the same file. Structural edges have `location=None`.
- FILE `IMPORTS` FILE; use the import location in the importing file.
  Binding metadata can support later resolution, but creates no symbol-import
  edge. Multiple distinct import sites can produce distinct edges.
- Callable SYMBOL `CALLS` callable SYMBOL at `CallSite.location`.
  Callable kinds are FUNCTION, METHOD, and CONSTRUCTOR. Classes are not
  callable endpoints; constructor calls require an extracted constructor.
- Type SYMBOL `INHERITS` type SYMBOL for a metadata-backed class-to-class
  or interface-to-interface base relationship. Class-to-interface inheritance
  is not emitted; TypeScript implements uses the separate edge kind below.
- CLASS or ENUM `IMPLEMENTS` INTERFACE when `implemented_types` supplies
  that relationship. Inheritance/implementation use the declaring location.

There are no redundant transitive structural edges: FILE -> Class -> Method
means FILE DEFINES Class and Class CONTAINS Method, never FILE DEFINES Method.
Resolve a non-null parent using exact `parent_qualified_name`, same-file
membership, and containing byte range. Require exactly one direct parent;
duplicate qualified names alone cannot choose it. An invalid or indeterminate
structural parent fails validation; it is not silently promoted to top level.

## Unresolved evidence and ownership

`ReferenceKind`: `IMPORT`, `CALL`, `INHERITS`, `IMPLEMENTS`.

`ResolutionFailureReason`: `EXTERNAL`, `NOT_FOUND`, `AMBIGUOUS`,
`UNSUPPORTED`, `INCOMPLETE_PARSE`.

`UnresolvedReference` contains `reference_id: str`,
`reference_kind: ReferenceKind`, `reason: ResolutionFailureReason`,
`repository_namespace: str`, `relative_path: str`,
`language: ParsedLanguage`, `source_node_id: str | None`,
`location: SourceLocation`, `raw_target: str`, and
`candidate_node_ids: tuple[str, ...]`.

Import evidence uses the FILE source and exact `ImportInfo.module` as raw
target. One import record is a module dependency resolution unit; its bindings
feed private lookup indexes. Missing symbol binding evidence can leave a later
call unresolved without invalidating a proven file import edge.
Call evidence uses exact `callee_text`; type evidence uses each exact entry
in `base_types` or `implemented_types`. Each distinct reference either yields
its proven edge or one unresolved record, with exact duplicates deduplicated.

Use `caller_qualified_name` plus same-file location containment to establish
one callable source. A null owner represents top-level or deliberately
unowned syntax; do not attach it to an enclosing callable by guesswork.
Such a call has no CALLS edge and has unresolved evidence with null source.
If ownership is missing because of incomplete parsing use INCOMPLETE_PARSE;
otherwise use UNSUPPORTED. A non-null owner that cannot identify one valid
callable cannot produce a resolved edge either.
For a non-null owner with multiple matching callable sources use AMBIGUOUS
with null source and empty target candidates; no matching source uses
INCOMPLETE_PARSE when relevant parse loss explains it, otherwise UNSUPPORTED.

Candidate IDs always refer to existing graph nodes, are unique and sorted
lexicographically by ID, and are evidence only. AMBIGUOUS includes the complete
eligible target candidate set when known. Other reasons use an empty tuple.
Caller ownership uncertainty is not represented as target candidate IDs.

## Resolution rules

Resolution is closed over the supplied inventories and supported metadata
shapes. It does not prove runtime behavior against monkey patching or external
environments. Names that need unavailable binding or type information remain
unresolved even when only one repository symbol happens to share a name.

For an eligible reference prefer evidence in this order:

1. Exact lexical/current scope.
2. Containing type.
3. Same file.
4. Explicit imported binding.
5. Uniquely resolved imported internal file with a supported member binding.
6. Uniquely qualified repository symbol anchored by the reference spelling
   or an explicit import, never an unqualified global coincidence.
7. Unresolved evidence.

This is language-aware precedence, not a universal scan through names.
Only semantically visible declarations qualify. A stronger ambiguous binding
or unsupported binding blocks falling through to a weaker apparent match.
Known parameter/name conflicts block unsafe binding resolution. Do not treat
class members as bare Python lexical function bindings. If lookup would require
unavailable local-variable, export, receiver, or runtime information, stop.

At the applicable tier, exactly one proven target gives an edge. Multiple
eligible targets give zero edges and one AMBIGUOUS reference. No lexical-sort
winner, first encountered file, shortest path, fuzzy match, or heuristic best
match is allowed. Sorting is only for canonical output.

Reason selection follows the first applicable condition:

1. The evidence shape needs unsupported semantics: UNSUPPORTED, unless known
   parse loss is the reason that required evidence is unavailable.
2. Relevant incomplete extraction prevents establishing a complete candidate
   set or safe ownership/binding: INCOMPLETE_PARSE.
3. A complete supported lookup has multiple eligible candidates: AMBIGUOUS.
4. A supported package/module import has no internal mapping or internal
   anchor: EXTERNAL. A call through that proven external import is EXTERNAL.
5. An explicit internal relative/qualified/binding lookup has no target:
   NOT_FOUND. A bare unknown call is NOT_FOUND, not an asserted external call.

Only relevant incomplete scopes/modules affect a reference. A failed unrelated
file does not poison all resolution. A successfully identified FILE remains a
valid import target even if its parse failed; missing members of that file are
INCOMPLETE_PARSE. Never call incomplete negative evidence definitive NOT_FOUND.

### Python

Use exact identifier/dotted-identifier metadata for supported static lookups.
Index root-relative `.py` module paths and package `__init__.py` paths without
consulting `sys.path`, installed packages, or configuration. If both module and
package candidates exist, preserve ambiguity; never select by filesystem order.
Do not infer extra source roots from arbitrary directory suffixes.

Relative leading dots use the importing file's package directory: one dot stays
in that package, each additional dot moves one level up. Escaping the inventory
root has no internal target. Absolute imports use the root-relative module
index. An unanchored package with no internal candidate is EXTERNAL.

Support same-file functions, explicit imported functions, relative imports,
`self.method` within a uniquely established instance-method/type context, and
`Class.method` with an explicitly resolved class and unique method declaration.
Honor parameter and modifier evidence; arbitrary receivers cannot be inferred
from their spelling. No runtime dispatch, inherited method search requiring
an MRO, or type inference is introduced.

Python imports contain no explicit import-form discriminator. If direct-import
and from-import interpretations of the actual fields differ, do not guess the
local binding. A common proven FILE dependency may still be emitted; dependent
calls are UNSUPPORTED unless their binding is independently established.
Multiple valid internal binding targets are AMBIGUOUS. Wildcards never authorize
an unqualified repository-wide search. `getattr`, `globals()[...]`, runtime
imports, and dynamic receiver expressions are UNSUPPORTED when represented as
call evidence. No extra evidence is fabricated for syntax Module 3 omitted.

### Java

Use package-qualified type names already encoded in `qualified_name` and
`ImportInfo.module`. Java imports have empty bindings and may carry the
`static` modifier. Derive imported type/member keys from those validated
strings. Do not add a package field or infer a package from directory layout.

Support explicit imports, package-qualified types, current-type methods,
`this.method`, and unique `Type.method` static declarations. Resolve base and
implemented type strings through the same scope/import rules, with endpoint
kind checks. Interface extends is INHERITS; class/enum implements is IMPLEMENTS.
Complex type expressions requiring semantic interpretation are UNSUPPORTED.

Wildcard candidate sets are limited to the named package/type and eligible
members. A wildcard import itself produces a FILE edge only when one file is
proven; otherwise it preserves unresolved evidence. A later specific member
lookup can still resolve uniquely from the bounded wildcard scope.
Multiple overloads are AMBIGUOUS: current `CallSite` has no argument evidence
to select by arity/type. `SymbolInfo.parameters` alone cannot disambiguate a
call. Constructors require one explicit constructor node. No implicit default
constructor, Java compiler, inherited dispatch engine, or generic type inference.

### JavaScript, JSX, TypeScript, TSX

Resolve relative module paths lexically within the repository. Explicit
supported extensions select the exact path; extensionless paths consider
`.js`, `.jsx`, `.ts`, `.tsx` and each corresponding `index` file under the
requested directory. Retain all eligible candidates; no extension-priority
winner or `.js` to `.ts` rewrite is inferred. Multiple candidates are AMBIGUOUS.
Paths outside the repository have no internal target.

Named imports use exact imported names and aliases. Default bindings use
`imported_name="default"`; namespace bindings use `imported_name="*"` and
an alias. Resolve exported declarations only when their recorded modifiers
and names establish the binding uniquely. Do not equate a default import
with the file's sole function. Re-exports and CommonJS export assignment
semantics lack a complete upstream export map and remain UNSUPPORTED where
that map is necessary. A module FILE edge can survive these binding limits.

Support metadata-backed local calls and TypeScript extends/implements.
JS/JSX/TS/TSX share ECMAScript module semantics where the supplied fields
suffice; no unrelated cross-language inference follows from that grouping.
Bare package imports are EXTERNAL without an explicit internal mapping.
Recognizable alias forms requiring configuration, such as `@/`, are
UNSUPPORTED; scoped package syntax such as `@scope/pkg` remains a package
specifier. Ordinary bare names cannot be identified as configured aliases
from these inventories. Do not read tsconfig.json, package manifests, or
node_modules. A future normalized upstream target could be used only after
contract review; no such field is assumed today.

### Cross-language boundary

Only direct static Module 3 evidence that uniquely establishes a relationship
can permit a cross-language edge. Strings, URLs, HTTP routes, subprocess
commands, reflection, FFI, configuration, and framework wiring do not qualify.
Current V1 resolvers do not invent bridges across Python, Java, and ECMAScript.

## Parse status and validation

SUCCESS permits all validated extracted evidence. PARTIAL retains its FILE,
trusted symbols, structural edges, and uniquely provable relationships.
Inspect `issues` and locations conservatively when determining relevant parse
loss; a locationless extraction/truncation issue cannot be assigned to an
invented smaller range. Never resolve a truncated name as if complete.

FAILED retains its FILE and status. The current parser produces empty symbols,
imports, and calls for failed files; require that contract. Do not fabricate
references from parse issues or missing source. A contradictory failed record
with extracted declarations is a validation failure, not a recovery heuristic.

Pass 0 validates values without filesystem operations:

- Exact expected inventory/record types, tuple collections, valid enum types,
  string/optional-string fields, boolean flags, nonnegative integer counters
  and sizes (booleans do not count as integers), and nested parameter/binding
  shapes. Validate optional digest syntax if present; do not recompute it.
- Namespace equality and nonempty string namespaces. Both repository path
  fields must be nonempty strings but are never resolved or used as identity;
  namespace is the authoritative repository association.
- Scanner counters match collections; parsed status and skipped counters match
  their collections. Parsed plus skipped paths exactly cover scanner SOURCE
  and TEST entries. Every parsed path has one compatible scanner selector.
- File paths are unique across each inventory's included/ignored/skipped
  categories. Reject contradictory or repeated file records rather than
  altering counters. No orphan parsed entries or ignored-file graph nodes.
- Paths must be canonical nonempty repository-relative slash paths: no leading
  slash, drive/UNC path, backslash, NUL, empty component, `.` or `..` component.
  Reject hostile paths; do not normalize them into valid inventory records.
  Relative import syntax is separately interpreted, not treated as a file path.
- Locations have nonnegative ordered byte offsets, positive ordered lines,
  nonnegative columns, and ordered columns on a single line. Original-file
  byte offsets must fit the matching `ScannedFile.size_bytes`; no source is
  reopened to reconstruct line/column coordinates.
- Symbol names and qualified names are nonempty. Parent references must yield
  the unique structural relationship described above. Validate nested metadata
  and locations, including optional issue locations, before indexing it.

Accept permutations of otherwise valid evidence; canonicalize internally.
Upstream output ordering is not a resolution input. Exact repeated imports,
calls, or identical symbol evidence may deduplicate. The same graph identity
with differing content, including differing resolution outcomes, is a hard
identity collision. No last-write or first-write winner is permitted.
Compare full upstream symbol evidence as well as projected node fields before
deduplication, so conflicting parents, modifiers, or type lists cannot vanish
when two records project to the same GraphNode.

## Multi-pass construction and invariants

0. Validate both inventories and their association.
1. Create FILE and SYMBOL nodes; build reusable declaration/module indexes.
2. Create direct DEFINES and CONTAINS edges.
3. Resolve imports into binding outcomes, then resolve calls and type
   relationships against those outcomes. Process all imports before dependent
   references; do not let file traversal order affect binding availability.
4. Materialize unresolved records from the resolution outcomes.
5. Check invariants, sort canonical tuples, derive immutable indexes, verify
   their consistency, and return `CodeGraphInventory`.

Before return require unique identities, one repository namespace, exactly one
known FILE for each SYMBOL, existing edge endpoints and candidate IDs, valid
endpoint kinds, and same-namespace relationships. Every SYMBOL has exactly one
structural incoming edge. Containment is acyclic. CALLS and IMPORTS cycles,
including recursion, are valid; no general graph DAG constraint is imposed.
Unresolved sources, when present, must exist in the evidence file. All index
entries must exactly reflect the canonical tuples. Never return speculative
edges or a partially validated inventory.

## Deterministic identity and canonical inventories

IDs are lowercase SHA-256 hex digests of UTF-8 encoded canonical JSON arrays,
using `ensure_ascii=False` and `separators=(",", ":")`. Enum values serialize
as their string values. No dictionaries enter identity payloads.

Define `range(location)` as `[start_byte, end_byte]`. Use JSON null for absent
edge locations and absent unresolved sources. Identity payloads are:

```text
FILE:
["tracerag-code-graph-node-v1", namespace, "file", relative_path]

SYMBOL:
["tracerag-code-graph-node-v1", namespace, "symbol", relative_path,
 symbol_kind, qualified_name, start_byte, end_byte]

EDGE:
["tracerag-code-graph-edge-v1", namespace, edge_kind,
 source_node_id, target_node_id, range(location) or null]

UNRESOLVED:
["tracerag-code-graph-unresolved-v1", namespace, relative_path,
 reference_kind, source_node_id or null, range(location), raw_target]
```

Failure reason and candidates are excluded from unresolved identity so improved
resolution does not rename the same source evidence. Full locations remain
model content: identical ID but inconsistent line/column data is a collision.
Parse status is not node identity. Source ranges disambiguate nested/repeated
definitions. Structural edges use the null location component. Distinct base
type strings use distinct unresolved raw targets even with a shared location.

Never include absolute paths, timestamps, PIDs, traversal indexes, UUIDs,
dictionary insertion order, or local filesystem case behavior. Preserve Unicode
and exact upstream spelling; do not globally lowercase paths or qualified names.

`CodeGraphInventory` has `repository_namespace: str`,
`nodes: tuple[GraphNode, ...]`, `edges: tuple[GraphEdge, ...]`, and
`unresolved_references: tuple[UnresolvedReference, ...]`, each sorted by its ID.
These tuples are authoritative. Derived indexes are:

- `outgoing_edges`: node ID -> tuple of edges sorted by edge ID.
- `incoming_edges`: node ID -> tuple of edges sorted by edge ID.
- `nodes_by_file`: relative path -> tuple of nodes sorted by node ID.
- `nodes_by_qualified_name`: exact qualified name -> tuple of SYMBOL nodes
  sorted by node ID, retaining cross-file and overload duplicates.

Edge indexes contain every node ID, including empty tuples for isolated nodes.
File indexes contain every FILE path. Qualified-name indexes omit FILE nodes.
Mapping keys have lexical order; values are tuples. Copy private backing maps
before exposing `MappingProxyType` or equivalent immutable mappings. No caller
can mutate tuples, nested values, or retained backing maps. No query engine,
graph cache, JSON/SQLite persistence, or graph database is part of V1.

## Exceptions

`CodeGraphError` is the public root. Its direct subclasses have distinct uses:

- `CodeGraphValidationError`: malformed or inconsistent upstream input.
- `CodeGraphIdentityError`: identity construction failure or same-ID conflict.
- `CodeGraphInvariantError`: constructed graph or index violates an invariant.
- `CodeGraphResolutionError`: resolver contract failure, such as an invalid
  outcome; ordinary ambiguity, missing targets, unsupported syntax, external
  references, and incomplete parsing are data, not exceptions.

Failures abort the build. Do not disguise internal resolver faults as
NOT_FOUND or silently discard evidence. No extra per-language exception tree.

## Performance and security

Let F be files, S symbols, R references, and E edges. File/declaration index
construction targets O(F + S); validating nested reference metadata also costs
O(R) plus its string/parameter content. Structural construction targets O(S),
using parent-key buckets and range indexes rather than rescanning all symbols.
Reference work targets O(R times bounded indexed lookup), plus actual candidate
enumeration. Canonical sorting costs O(n log n). Broad wildcard/ambiguity sets
necessarily cost their output size; count that cost explicitly, never hide it
as constant time. Avoid O(R times S) global scans and repeated ancestor walks.

Private indexes include symbols by file/name, qualified name, parent/range,
files by module candidate, and imports by local binding/scope. Build once;
reuse precomputed scope relationships and memoized lookup outcomes as needed.
Temporary indexes and final graph use memory proportional to input, output,
and materialized candidate evidence, not an all-symbol-pairs matrix.

Repository content is untrusted data. Module 6 must not reopen source, use
`Path.read_text`/`read_bytes` or `open` for repository data, execute/import
repository modules, use eval/exec, subprocess project code, invoke Maven,
Gradle, npm, or pip, inspect node_modules, read .env, access the network,
invoke Gemini, query Chroma, or use unsafe deserialization. Resolvers consume
only validated strings, enums, locations, metadata, and indexes.

The source-of-truth chain is Module 1 -> Module 2 FileInventory -> Module 3
CodeParseInventory -> Module 6 derivation. No hidden source reread may resolve
uncertainty. The returned graph is in memory only.

## Test strategy and eventual completion gates

Expected focused groups (the later implementation plan may refine filenames):

- `tests/test_code_graph_models.py`: frozen/slotted values, enum/public API
  contracts, nested immutability, read-only indexes and isolated nodes.
- `tests/test_code_graph_identities.py`: exact payloads, Unicode, namespace
  changes, range disambiguation, nesting, collisions, and reason-independent IDs.
- `tests/test_code_graph_validation.py`: counters, selectors, optional upstream
  namespace rejection, paths, orphan/duplicate records, ranges, parent ambiguity.
- `tests/test_code_graph_builder.py`: FILE nodes, top-level DEFINES, direct
  CONTAINS, multiple files, empty inventories, and all graph invariants.
- `tests/test_code_graph_python.py`: same-file call, self.method, imported call,
  relative import, module/package ambiguity, external package, dynamic calls,
  ambiguous import forms and binding/ownership limitations.
- `tests/test_code_graph_java.py`: package import, this.method, type-qualified
  call, inheritance, implements, wildcard ambiguity, overload ambiguity,
  constructor eligibility and interface inheritance.
- `tests/test_code_graph_ecmascript.py`: relative/named imports, supported
  namespace/default exports, extension/index candidates, package external,
  candidate ambiguity, TS extends/implements, unsupported aliases/re-exports.
- `tests/test_code_graph_partial_parses.py`: trusted PARTIAL evidence survives,
  INCOMPLETE_PARSE versus NOT_FOUND, failed FILE retention, no fabricated
  references, locationless truncation issues and irrelevant partial files.
- `tests/test_code_graph_determinism.py`: shuffled inventories and nested
  evidence, exact duplicate deduplication, stable IDs/tuples/indexes, no
  first-candidate winner, zero ambiguous edges, one unresolved record and
  sorted candidate IDs; valid import/call cycles and invalid containment cycles.
- `tests/test_code_graph_security.py`: build from prepared in-memory inventories
  with forbidden APIs patched to fail, including builtins.open, Path.read_text,
  Path.read_bytes, subprocess APIs, importlib.import_module, and network entry
  points. Initialize trusted test dependencies before patching. Hostile metadata
  remains inert; no source-reader or parser invocation occurs during graph build.
- `tests/test_code_graph_scaling.py`: indexed operation counts/instrumentation
  for increasing files, symbols, scope depth, calls and wildcard candidates;
  no fragile fixed wall-clock thresholds or repeated global symbol scans.
- `tests/test_code_graph_integration.py`: real Module 2 output plus real Module 3
  output produces the expected graph and unresolved evidence. Source reading
  belongs only to upstream fixture generation, never the graph build.

Before eventual Module 6 completion require Module 1 regression, Module 2
regression, Module 3 plus grammar gate, Module 4 regression, Module 5 regression,
Module 6 focused suite, full offline suite, compileall, pip check, and
`git diff --check`. These are future implementation gates, not claims that
implementation exists or that those suites ran for this documentation change.
No production changes to Modules 1–5 are required. If implementation uncovers
a material upstream contract deficiency, stop for design review.

## Strict non-goals

No control-flow or data-flow graph, variable read/write graph, pointer analysis,
compiler-grade type inference, Java/TypeScript compiler integration, Python
runtime dispatch, reflection analysis, framework route wiring, API inference,
dependency installation, persistence, NetworkX, graph database, Git history,
runtime traces/logs, embeddings/vector search, semantic retrieval/reranking,
LLM reasoning, causal ranking, or frontend visualization. All Modules 7–10
functionality is excluded. This specification does not create implementation
code, an implementation plan, or a feature worktree.
