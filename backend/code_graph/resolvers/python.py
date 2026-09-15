"""Resolve Python import evidence against immutable graph indexes."""

import keyword

from backend.code_graph.indexes import GraphIndexes
from backend.code_graph.models import ResolutionFailureReason
from backend.code_graph.resolvers.base import (
    Binding,
    ImportResult,
    Resolution,
    choose_resolution,
    import_scope,
)
from backend.code_parser.models import ImportInfo, ParsedFile, ParseStatus


def _is_identifier(value: str) -> bool:
    return value.isidentifier() and not keyword.iskeyword(value)


def _is_dotted_identifier(value: str) -> bool:
    return all(_is_identifier(part) for part in value.split("."))


def _module_candidates(
    index: GraphIndexes, file: ParsedFile, module: str
) -> tuple[tuple[str, ...], bool, bool]:
    level = len(module) - len(module.lstrip("."))
    tail_text = module[level:]
    if tail_text and not _is_dotted_identifier(tail_text):
        return (), False, True

    if not level:
        return index.python_modules.get(module, ()), True, False

    parts = file.relative_path.split("/")[:-1]
    if not parts or level - 1 > len(parts):
        return (), False, False
    anchor = parts[: len(parts) - (level - 1)]
    tail = tail_text.split(".") if tail_text else []
    key = ".".join(anchor + tail)
    return index.python_modules.get(key, ()), False, False


def _member_resolution(
    index: GraphIndexes,
    module_resolution: Resolution,
    imported_name: str,
) -> Resolution:
    if not _is_identifier(imported_name):
        return choose_resolution((), unsupported=True)
    if module_resolution.target_node_id is None:
        if module_resolution.reason is ResolutionFailureReason.AMBIGUOUS:
            return Resolution(None, ResolutionFailureReason.AMBIGUOUS)
        return module_resolution

    target_id = module_resolution.target_node_id
    target = index.nodes_by_id[target_id]
    candidate_ids = tuple(
        node_id
        for node_id in index.by_file_name.get(
            (target.relative_path, imported_name), ()
        )
        if index.parent_ids.get(node_id) is None
    )
    incomplete = (
        not candidate_ids
        and index.files_by_path[target.relative_path].status
        in (ParseStatus.PARTIAL, ParseStatus.FAILED)
    )
    return choose_resolution(candidate_ids, incomplete=incomplete)


def _is_indistinguishable_form(item: ImportInfo) -> bool:
    return (
        len(item.bindings) == 1
        and item.bindings[0].imported_name == item.module
        and "." not in item.module
    )


def _binding_local_name(imported_name: str, alias: str | None) -> str:
    return alias if alias is not None else imported_name.split(".", 1)[0]


def resolve_import(
    index: GraphIndexes, file: ParsedFile, item: ImportInfo
) -> ImportResult:
    """Resolve one Python import record."""

    scope_node_id, scope_reason = import_scope(index, file, item)
    candidate_ids, is_absolute, unsupported_module = _module_candidates(
        index, file, item.module
    )
    module_resolution = choose_resolution(
        candidate_ids,
        unsupported=unsupported_module,
        external=is_absolute and not candidate_ids,
    )

    bindings: list[Binding] = []
    indistinguishable = _is_indistinguishable_form(item)
    for imported in item.bindings:
        local_name = _binding_local_name(imported.imported_name, imported.alias)
        may_be_direct = (
            len(item.bindings) == 1
            and imported.imported_name == item.module
            and not item.module.startswith(".")
        )
        imported_name_valid = (
            _is_dotted_identifier(imported.imported_name)
            if may_be_direct
            else _is_identifier(imported.imported_name)
        )
        alias_valid = imported.alias is None or _is_identifier(imported.alias)
        if not imported_name_valid or not alias_valid:
            bindings.append(
                Binding(
                    local_name,
                    scope_node_id,
                    "blocked",
                    Resolution(None, ResolutionFailureReason.UNSUPPORTED),
                )
            )
            continue

        if indistinguishable:
            symbol_interpretation = _member_resolution(
                index, module_resolution, imported.imported_name
            )
            common_resolution = (
                module_resolution
                if module_resolution == symbol_interpretation
                else Resolution(None, ResolutionFailureReason.UNSUPPORTED)
            )
            bindings.append(
                Binding(local_name, scope_node_id, "blocked", common_resolution)
            )
            continue

        is_direct_module = (
            may_be_direct
            and "." in imported.imported_name
        )
        if is_direct_module:
            mode = "module"
            binding_resolution = module_resolution
        else:
            mode = "symbol"
            binding_resolution = _member_resolution(
                index, module_resolution, imported.imported_name
            )

        if item.modifiers or scope_reason is not None:
            mode = "blocked"
            binding_resolution = Resolution(
                None,
                ResolutionFailureReason.UNSUPPORTED
                if item.modifiers
                else scope_reason,
            )
        bindings.append(
            Binding(local_name, scope_node_id, mode, binding_resolution)
        )

    return ImportResult(module_resolution, tuple(bindings))
