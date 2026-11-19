from pathlib import Path
import pytest
from openwispr.model import (
    GGML_MAGIC,
    file_name,
    is_english_only,
    looks_valid,
    url_for,
    validate_name,
)


def test_names_and_urls():
    assert file_name("base.en") == "ggml-base.en.bin"
    assert (
        url_for("tiny.en")
        == "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
    )


def test_english_only_detection():
    assert is_english_only("base.en")
    assert is_english_only("small.en-q5_1")
    assert not is_english_only("base")
    assert not is_english_only("large-v3-turbo-q5_0")


def test_rejects_unsafe_names():
    validate_name("base.en")
    validate_name("large-v3-turbo-q5_0")
    with pytest.raises(ValueError):
        validate_name("../evil")
    with pytest.raises(ValueError):
        validate_name("a/b")
    with pytest.raises(ValueError):
        validate_name("")


def test_validity_check(tmp_path: Path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"not a model")
    assert not looks_valid(bad)

    good = tmp_path / "good.bin"
    data = bytearray(GGML_MAGIC)
    data.extend(b"\x00" * 200_000)
    good.write_bytes(bytes(data))
    assert looks_valid(good)

    assert not looks_valid(tmp_path / "missing.bin")
