import pytest

from backend.runtime_evidence.validation import (
    require_relative_posix_path, require_scalar_text, truncate_utf8_text,
    utf8_size,
)


def test_scalar_validation_rejects_every_surrogate_and_nul():
    for bad in ("\ud800", "\udfff", "a\ud800b", "\ud83d\ude00", "a\x00b"):
        with pytest.raises(ValueError):
            require_scalar_text(bad, "field", ValueError)
    assert require_scalar_text("😀", "field", ValueError) == "😀"


def test_scalar_validation_rejects_non_strings():
    for bad in (None, 1, b"x", object()):
        with pytest.raises(ValueError):
            require_scalar_text(bad, "field", ValueError)


def test_multibyte_byte_limits_are_utf8_bytes_not_characters():
    assert utf8_size("é") == 2
    assert require_scalar_text("é" * 128, "namespace", ValueError,
                               max_bytes=256) == "é" * 128
    with pytest.raises(ValueError):
        require_scalar_text("é" * 128 + "a", "namespace", ValueError,
                            max_bytes=256)


@pytest.mark.parametrize("bad", ["", "/a", "C:/a", "C:a", r"a\b",
                                 "a//b", "a/./b", "a/../b", "a/", "a\x00b",
                                 "a\x01b", "a\tb", "a\ud800b"])
def test_relative_posix_path_rejects_unsafe_forms(bad):
    with pytest.raises(ValueError):
        require_relative_posix_path(bad, "path", ValueError)


def test_relative_posix_path_accepts_canonical_components():
    assert require_relative_posix_path("pkg/app.py", "path", ValueError) == "pkg/app.py"


def test_text_truncation_is_codepoint_safe_and_reserves_ellipsis():
    assert truncate_utf8_text("abc", 3) == ("abc", False)
    assert truncate_utf8_text("abc😀", 5) == ("ab…", True)
    assert truncate_utf8_text("😀x", 4) == ("", True)
    assert truncate_utf8_text("x", 1) == ("x", False)
