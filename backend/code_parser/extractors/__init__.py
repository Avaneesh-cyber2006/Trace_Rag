"""Language adapters and shared extraction infrastructure."""

from .base import BaseExtractor, ExtractionResult, get_extractor as _get_extractor
from .java import JavaExtractor
from .python import PythonExtractor


def get_extractor(extractor_key: str) -> BaseExtractor:
    """Return the registered extractor for a trusted language key."""

    if extractor_key == "python":
        return PythonExtractor()
    if extractor_key == "java":
        return JavaExtractor()
    return _get_extractor(extractor_key)


__all__ = (
    "BaseExtractor",
    "ExtractionResult",
    "JavaExtractor",
    "PythonExtractor",
    "get_extractor",
)
