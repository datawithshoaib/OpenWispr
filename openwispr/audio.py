"""
Microphone capture and conversion to the 16 kHz mono f32 audio Whisper expects.
"""

from __future__ import annotations

import math
import struct
import sys
import threading
import wave
from pathlib import Path
import numpy as np
import sounddevice as sd

WHISPER_RATE: int = 16_000

_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_IEEE_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE


def resample(input_data: np.ndarray | list[float], from_rate: int, to_rate: int) -> np.ndarray:
    """
    Converts mono audio between sample rates.
    Downsampling averages each output sample's span of input (cheap anti-aliasing);
    upsampling interpolates linearly.
    """
    arr = np.asarray(input_data, dtype=np.float32)
    if from_rate == to_rate or len(arr) == 0:
        return arr.copy()

    ratio = float(from_rate) / float(to_rate)
    out_len = int(math.floor(len(arr) / ratio))
    if out_len == 0:
        return np.array([], dtype=np.float32)

    if ratio > 1.0:
        idx_start = (np.arange(out_len, dtype=np.float64) * ratio).astype(np.int64)
        idx_end = np.clip(
            ((np.arange(out_len, dtype=np.float64) + 1.0) * ratio).astype(np.int64),
            idx_start + 1,
            len(arr),
        )
        cs = np.pad(np.cumsum(arr, dtype=np.float64), (1, 0))
        out = ((cs[idx_end] - cs[idx_start]) / (idx_end - idx_start)).astype(np.float32)
    else:
        pos = np.arange(out_len, dtype=np.float64) * ratio
        idx = pos.astype(np.int64)
        frac = (pos - idx).astype(np.float32)
        a = arr[idx]
        b = np.where(idx + 1 < len(arr), arr[np.minimum(idx + 1, len(arr) - 1)], a)
        out = (a + (b - a) * frac).astype(np.float32)

    return out


def rms(samples: np.ndarray | list[float]) -> float:
    """Calculates RMS (root mean square) energy of the audio samples."""
    arr = np.asarray(samples, dtype=np.float32)
    if len(arr) == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


def is_silence(samples: np.ndarray | list[float]) -> bool:
    """
    True when the clip is too short or too quiet to contain speech.
    Skipping these avoids Whisper hallucinating text from silence.
    """
    arr = np.asarray(samples, dtype=np.float32)
    return len(arr) < (WHISPER_RATE // 4) or rms(arr) < 0.0015


def load_wav(path: str | Path) -> np.ndarray:
    """
    Loads a PCM (8/16/24/32-bit) or IEEE float (32/64-bit) WAV file, including
    WAVE_FORMAT_EXTENSIBLE files, as 16 kHz mono f32.
    """
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"cannot open {p}")

    blob = p.read_bytes()
    if len(blob) < 12 or blob[0:4] != b"RIFF" or blob[8:12] != b"WAVE":
        raise ValueError(f"{p} is not a WAV file")

    fmt: bytes | None = None
    raw: bytes | None = None
    pos = 12
    while pos + 8 <= len(blob):
        chunk_id = blob[pos : pos + 4]
        size = struct.unpack_from("<I", blob, pos + 4)[0]
        body = blob[pos + 8 : pos + 8 + size]
        if chunk_id == b"fmt ":
            fmt = body
        elif chunk_id == b"data":
            raw = body
        pos += 8 + size + (size & 1)  # chunks are word-aligned
    if fmt is None or len(fmt) < 16 or raw is None:
        raise ValueError(f"{p}: malformed WAV (missing fmt or data chunk)")

    format_tag, channels, framerate, _, block_align, bits = struct.unpack_from("<HHIIHH", fmt)
    if format_tag == _WAVE_FORMAT_EXTENSIBLE and len(fmt) >= 26:
        format_tag = struct.unpack_from("<H", fmt, 24)[0]  # first two bytes of SubFormat GUID
    if channels < 1:
        raise ValueError(f"{p}: invalid channel count {channels}")

    width = block_align // channels if block_align else (bits + 7) // 8
    raw = raw[: len(raw) - len(raw) % (width * channels)]

    if format_tag == _WAVE_FORMAT_IEEE_FLOAT and width in (4, 8):
        data = np.frombuffer(raw, dtype="<f4" if width == 4 else "<f8").astype(np.float32)
    elif format_tag == _WAVE_FORMAT_PCM and width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif format_tag == _WAVE_FORMAT_PCM and width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif format_tag == _WAVE_FORMAT_PCM and width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        data = ints.astype(np.float32) / 8388608.0
    elif format_tag == _WAVE_FORMAT_PCM and width == 4:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"{p}: unsupported WAV encoding (format {format_tag}, {bits}-bit)")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    return resample(data, framerate, WHISPER_RATE)


def save_wav(path: str | Path, samples: np.ndarray | list[float]) -> None:
    """Saves 16 kHz mono audio as a 16-bit WAV file."""
    arr = np.asarray(samples, dtype=np.float32)
    clamped = np.clip(arr, -1.0, 1.0)
    int16_data = (clamped * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(WHISPER_RATE)
        w.writeframes(int16_data.tobytes())


def list_devices() -> list[tuple[str, bool]]:
    """Lists input device names, marking the default one."""
    devices = sd.query_devices()
    default_input = sd.default.device[0]
    result: list[tuple[str, bool]] = []
    for idx, d in enumerate(devices):
        if d["max_input_channels"] > 0:
            name = d["name"]
            is_default = (idx == default_input)
            result.append((name, is_default))
    return result


def find_input_device(name: str | None) -> tuple[int | None, int, int]:
    devices = sd.query_devices()
    default_idx = sd.default.device[0]
    if name is None:
        if default_idx is None or default_idx < 0:
            raise RuntimeError("no microphone found (check Windows sound settings)")
        d_info = sd.query_devices(default_idx)
        channels = min(d_info["max_input_channels"], 2)
        rate = int(d_info["default_samplerate"])
        return default_idx, rate, max(1, channels)

    wanted = name.lower()
    for idx, d in enumerate(devices):
        if d["max_input_channels"] > 0 and wanted in d["name"].lower():
            channels = min(d["max_input_channels"], 2)
            rate = int(d["default_samplerate"])
            return idx, rate, max(1, channels)
    raise RuntimeError(f"no microphone matching '{name}' (see `openwispr devices`)")


class Recorder:
    """
    Records from an input device while active. The stream is opened on `start`
    and closed on `stop`, so the microphone is only in use while dictating.
    """

    def __init__(self, device_name: str | None = None) -> None:
        self.device_name = device_name
        self._stream: sd.InputStream | None = None
        self._chunks: list[np.ndarray] = []
        self._rate: int = 16_000
        self._lock = threading.Lock()

    def is_recording(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        device_idx, rate, channels = find_input_device(self.device_name)
        self._rate = rate
        self._chunks = []

        def callback(indata, frames, time_info, status):
            if status:
                sys.stderr.write(f"microphone error: {status}\n")
                sys.stderr.flush()
            if indata.shape[1] > 1:
                mono = indata.mean(axis=1)
            else:
                mono = indata[:, 0]
            with self._lock:
                self._chunks.append(mono.copy())

        stream = sd.InputStream(
            device=device_idx,
            channels=channels,
            samplerate=rate,
            dtype="float32",
            callback=callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        # Only mark as recording once the stream is actually running.
        self._stream = stream

    def stop(self) -> np.ndarray:
        if self._stream is None:
            return np.array([], dtype=np.float32)
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None

        with self._lock:
            if not self._chunks:
                return np.array([], dtype=np.float32)
            recorded = np.concatenate(self._chunks)
            self._chunks = []

        return resample(recorded, self._rate, WHISPER_RATE)
