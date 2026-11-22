"""
Locating and downloading free Whisper models (ggml format, from the whisper.cpp
Hugging Face repository).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
import requests

BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

# Silero voice-activity detector (under 1 MB), used to ignore clips without speech.
VAD_URL = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
VAD_FILE = "ggml-silero-v5.1.2.bin"

# The model used when none is specified: better accuracy, still light on 8 GB RAM.
DEFAULT_MODEL = "small.en"

# Models known to exist in the whisper.cpp repository, with approximate download size.
KNOWN_MODELS: list[tuple[str, str]] = [
    ("tiny.en", "75 MB   fastest, lowest accuracy (English)"),
    ("base.en", "142 MB  fast, good accuracy (English)"),
    ("base.en-q5_1", "57 MB   quantized base.en, slightly less accurate"),
    ("small.en", "466 MB  slower, better accuracy (English) [default]"),
    ("small.en-q5_1", "181 MB  quantized small.en, faster"),
    ("tiny", "75 MB   multilingual"),
    ("base", "142 MB  multilingual"),
    ("small", "466 MB  multilingual"),
    ("large-v3-turbo-q5_0", "547 MB  multilingual, best accuracy, needs a fast CPU"),
]

# ggml model files start with the magic number 0x67676d6c ("ggml"), stored little-endian.
GGML_MAGIC = b"lmgg"


def models_dir() -> Path:
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
    return base / "OpenWispr" / "models"


def file_name(model: str) -> str:
    return f"ggml-{model}.bin"


def url_for(model: str) -> str:
    return f"{BASE_URL}/{file_name(model)}"


def is_english_only(model: str) -> bool:
    """English-only models end in .en (optionally followed by a quantization suffix)."""
    return model.split("-")[0].endswith(".en")


def validate_name(model: str) -> None:
    """Rejects names that could escape the models directory or build a bogus URL."""
    ok = (
        bool(model)
        and all(c.isalnum() or c in ".-_" for c in model)
        and ".." not in model
    )
    if not ok:
        raise ValueError(f"invalid model name '{model}' (try one of: openwispr models)")


def looks_valid(path: str | Path) -> bool:
    """Returns True if the file looks like a complete ggml model."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        if p.stat().st_size <= 100_000:
            return False
        with open(p, "rb") as f:
            magic = f.read(4)
            return magic == GGML_MAGIC
    except OSError:
        return False


def ensure(model: str) -> Path:
    """
    Resolves model to a local file, downloading it first if needed.
    model may be a catalog name (e.g. base.en) or a path to a .bin file.
    """
    as_path = Path(model)
    if as_path.suffix == ".bin":
        if not looks_valid(as_path):
            raise ValueError(f"{as_path} is not a valid ggml Whisper model")
        return as_path.resolve()

    validate_name(model)
    path = models_dir() / file_name(model)
    if looks_valid(path):
        return path

    url = url_for(model)
    label = f"Whisper model '{model}'"
    try:
        download(url, path, label)
    except Exception as e:
        raise RuntimeError(
            f"could not get model '{model}' (is the name right? see `openwispr models`): {e}"
        ) from e
    return path


def ensure_vad() -> Path:
    """Resolves the voice-activity-detection model, downloading it if needed."""
    path = models_dir() / VAD_FILE
    if not looks_valid(path):
        download(VAD_URL, path, "voice detection model")
    return path


def download(url: str, dest: Path, label: str) -> None:
    """
    Downloads url to dest, writing to a .part file first so an interrupted
    download never leaves a truncated model behind.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    sys.stderr.write(f"Downloading {label}\n  from {url}\n  to   {dest}\n")
    sys.stderr.flush()

    response = requests.get(url, stream=True, timeout=30)
    response.raise_for_status()

    total_header = response.headers.get("content-length")
    total = int(total_header) if total_header and total_header.isdigit() else None

    part = dest.with_suffix(".bin.part")
    chunk_size = 64 * 1024
    done = 0
    start = time.time()
    last_pct = -1

    try:
        with open(part, "wb") as out:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // max(1, total)
                        if pct != last_pct:
                            last_pct = pct
                            elapsed = max(0.001, time.time() - start)
                            mbps = (done / 1e6) / elapsed
                            sys.stderr.write(
                                f"\r  {pct:3}%  {done / 1e6:.0f}/{total / 1e6:.0f} MB  {mbps:.1f} MB/s "
                            )
                            sys.stderr.flush()
        sys.stderr.write("\n")
        sys.stderr.flush()

        if total is not None and done != total:
            part.unlink(missing_ok=True)
            raise IOError(f"download incomplete: got {done} of {total} bytes")

        if not looks_valid(part):
            part.unlink(missing_ok=True)
            raise ValueError("downloaded file is not a ggml model")

        part.replace(dest)
        sys.stderr.write(f"  done in {time.time() - start:.1f}s\n")
        sys.stderr.flush()
    except Exception:
        part.unlink(missing_ok=True)
        raise
