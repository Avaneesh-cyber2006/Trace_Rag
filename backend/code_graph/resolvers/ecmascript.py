"""Resolve ECMAScript import evidence against immutable graph indexes."""

from backend.code_graph.indexes import GraphIndexes
from backend.code_graph.models import ResolutionFailureReason
from backend.code_graph.resolvers.base import (
    Binding,
    ImportResult,
    Resolution,
    choose_resolution,
    import_scope,
)
from backend.code_parser.models import (
    ImportBinding,
    ImportInfo,
    ParsedFile,
    ParsedLanguage,
    ParseStatus,
)


_ECMASCRIPT_LANGUAGES = (
    ParsedLanguage.JAVASCRIPT,
    ParsedLanguage.TYPESCRIPT,
    ParsedLanguage.TSX,
)
_EXTENSIONS = (".js", ".jsx", ".ts", ".tsx")


def _relative_candidates(
    index: GraphIndexes, file: ParsedFile, module: str
) -> tuple[tuple[str, ...], bool]:
    if (
        not module
        or "\\" in module
        or "\x00" in module
        or module.startswith("/")
        or not module.startswith(("./", "../"))
    ):
        return (), True

    components = module.split("/")
    if any(component == "" for component in components):
        return (), True

    stack = file.relative_path.split("/")[:-1]
    appended = False
    for component in components:
        if component == ".":
            continue
        if component == "..":
            if not stack:
                return (), False
            stack.pop()
            continue
        stack.append(component)
        appended = True

    if not appended:
        return (), True
    normalized = "/".join(stack)
    if normalized.endswith(_EXTENSIONS):
        paths = (normalized,)
    else:
        filename = normalized.rsplit("/", 1)[-1]
        if "." in filename:
            return (), True
        paths = tuple(
            path
            for extension in _EXTENSIONS
            for path in (normalized + extension, normalized + "/index" + extension)
        )

    candidate_ids = tuple(
        index.file_ids[path]
        for path in paths
        if path in index.file_ids
        and index.files_by_path[path].language in _ECMASCRIPT_LANGUAGES
    )
    return candidate_ids, False


def _module_resolution(
    index: GraphIndexes, file: ParsedFile, module: str
) -> Resolution:
    if file.language not in _ECMASCRIPT_LANGUAGES:
        return choose_resolution((), unsupported=True)
    if module.startswith("@/"):
        return choose_resolution((), unsupported=True)
    if module.startswith(("./", "../")):
        candidates, unsupported = _relative_candidates(index, file, module)
        return choose_resolution(candidates, unsupported=unsupported)
    if (
        not module
        or module.startswith((".", "/"))
        or "\\" in module
        or "\x00" in module
    ):
        return choose_resolution((), unsupported=True)
    return choose_resolution((), external=True)


def _module_file_ids(resolution: Resolution) -> tuple[str, ...]:
    if resolution.target_node_id is not None:
        return (resolution.target_node_id,)
    if resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        return resolution.candidate_node_ids
    return ()


def _exported_ids(
    index: GraphIndexes,
    file_id: str,
    imported_name: str,
) -> tuple[str, ...]:
    path = index.nodes_by_id[file_id].relative_path
    if imported_name == "default":
        candidates = index.children[file_id]
    else:
        candidates = index.by_file_name.get((path, imported_name), ())

    exported: list[str] = []
    for node_id in candidates:
        if index.parent_ids.get(node_id) is not None:
            continue
        symbol = index.symbols_by_id[node_id]
        modifiers = symbol.modifiers
        if "export" not in modifiers:
            continue
        if imported_name == "default":
            if "default" in modifiers:
                exported.append(node_id)
        elif "default" not in modifiers and symbol.name == imported_name:
            exported.append(node_id)
    return tuple(exported)


def _member_resolution(
    index: GraphIndexes,
    module_resolution: Resolution,
    imported_name: str,
) -> Resolution:
    if (
        module_resolution.reason is not None
        and module_resolution.reason is not ResolutionFailureReason.AMBIGUOUS
    ):
        return module_resolution

    file_ids = _module_file_ids(module_resolution)
    candidate_ids = tuple(
        node_id
        for file_id in file_ids
        for node_id in _exported_ids(index, file_id, imported_name)
    )
    if module_resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        return Resolution(
            None,
            ResolutionFailureReason.AMBIGUOUS,
            tuple(sorted(set(candidate_ids))),
        )
    if candidate_ids:
        return choose_resolution(candidate_ids)

    target_file = index.files_by_path[
        index.nodes_by_id[file_ids[0]].relative_path
    ]
    if target_file.status in (ParseStatus.PARTIAL, ParseStatus.FAILED):
        return Resolution(None, ResolutionFailureReason.INCOMPLETE_PARSE)
    return Resolution(None, ResolutionFailureReason.UNSUPPORTED)


def _local_name(binding: ImportBinding) -> str:
    return binding.alias if binding.alias is not None else binding.imported_name


def _item_shape_supported(item: ImportInfo) -> bool:
    namespace_count = sum(
        binding.imported_name == "*" for binding in item.bindings
    )
    if item.is_wildcard:
        return namespace_count == 1 and all(
            binding.imported_name in ("default", "*")
            for binding in item.bindings
        )
    return namespace_count == 0


def _binding_shape_supported(binding: ImportBinding) -> bool:
    if binding.imported_name == "*":
        return binding.alias is not None and bool(binding.alias)
    if not binding.imported_name:
        return False
    if binding.alias is not None and not binding.alias:
        return False
    if binding.imported_name == "default" and binding.alias is None:
        return False
    return True


def _ambiguous_with_known_candidates(resolution: Resolution) -> Resolution:
    if resolution.target_node_id is not None:
        candidates = (resolution.target_node_id,)
    elif resolution.reason is ResolutionFailureReason.AMBIGUOUS:
        candidates = resolution.candidate_node_ids
    else:
        candidates = ()
    return Resolution(None, ResolutionFailureReason.AMBIGUOUS, candidates)


def resolve_import(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> ImportResult:
    """Resolve one ECMAScript import record without reading project files."""

    scope_node_id, scope_reason = import_scope(index, file, item)
    module_resolution = _module_resolution(index, file, item.module)
    bindings: list[Binding] = []
    item_shape_supported = not item.modifiers and _item_shape_supported(item)

    for imported in item.bindings:
        local_name = _local_name(imported)
        if not local_name or (
            imported.imported_name == "*" and imported.alias is None
        ):
            continue
        if not item_shape_supported or not _binding_shape_supported(imported):
            bindings.append(
                Binding(
                    local_name,
                    scope_node_id,
                    "blocked",
                    Resolution(None, ResolutionFailureReason.UNSUPPORTED),
                )
            )
            continue

        if imported.imported_name == "*":
            mode = "module"
            binding_resolution = module_resolution
        else:
            mode = "symbol"
            binding_resolution = _member_resolution(
                index, module_resolution, imported.imported_name
            )

        if scope_reason is ResolutionFailureReason.AMBIGUOUS:
            mode = "blocked"
            binding_resolution = _ambiguous_with_known_candidates(
                binding_resolution
            )
        elif scope_reason is not None:
            mode = "blocked"
            binding_resolution = Resolution(None, scope_reason)
        elif binding_resolution.reason is ResolutionFailureReason.UNSUPPORTED:
            mode = "blocked"

        bindings.append(
            Binding(local_name, scope_node_id, mode, binding_resolution)
        )

    return ImportResult(module_resolution, tuple(bindings))
