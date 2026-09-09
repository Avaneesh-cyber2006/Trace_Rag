"""Private process-local mutation coordination shared by core and adapters."""

from contextlib import contextmanager
import os
from threading import Lock
from typing import Iterator

from .exceptions import EmbeddingVectorStoreConfigurationError


_WRITER_GUARDS_LOCK = Lock()
_ACTIVE_WRITER_GUARDS: set[tuple[object, str]] = set()


def _reset_inherited_writer_guards() -> None:
    """Child processes must not inherit thread locks or active writer entries."""
    global _WRITER_GUARDS_LOCK, _ACTIVE_WRITER_GUARDS
    _WRITER_GUARDS_LOCK = Lock()
    _ACTIVE_WRITER_GUARDS = set()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_inherited_writer_guards)


@contextmanager
def _repository_writer_guard(store: object, repository_namespace: str) -> Iterator[None]:
    """Acquire one nonblocking mutation lease for an exact namespace."""
    key_factory = getattr(store, "_writer_guard_key", None)
    scope = key_factory(repository_namespace) if callable(key_factory) else id(store)
    try:
        key = (scope, repository_namespace)
        hash(key)
    except (TypeError, ValueError) as error:
        raise EmbeddingVectorStoreConfigurationError(
            "Semantic indexer dependency configuration is invalid."
        ) from error
    with _WRITER_GUARDS_LOCK:
        if key in _ACTIVE_WRITER_GUARDS:
            raise EmbeddingVectorStoreConfigurationError(
                "Repository index mutation is already in progress."
            )
        _ACTIVE_WRITER_GUARDS.add(key)
    try:
        yield
    finally:
        with _WRITER_GUARDS_LOCK:
            _ACTIVE_WRITER_GUARDS.discard(key)
