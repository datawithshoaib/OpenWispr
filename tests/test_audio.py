import math
import tempfile
import wave
from pathlib import Path
import numpy as np
import pytest

from openwispr.audio import (
    WHISPER_RATE,
    is_silence,
    load_wav,
    resample,
    rms,
    save_wav,
)


def sine(freq: float, rate: int, secs: float) -> list[float]:
    n = int(rate * secs)
    return [
        math.sin(i / rate * freq * 2.0 * math.pi) * 0.5
        for i in range(n)
    ]


def test_resample_identity():
    x = [0.1, 0.2, 0.3]
    out = resample(x, 16_000, 16_000)
    np.testing.assert_allclose(out, x, rtol=1e-5, atol=1e-5)


def test_resample_lengths():
    assert len(resample(np.zeros(48_000, dtype=np.float32), 48_000, 16_000)) == 16_000
    assert len(resample(np.zeros(44_100, dtype=np.float32), 44_100, 16_000)) == 16_000
    assert len(resample(np.zeros(8_000, dtype=np.float32), 8_000, 16_000)) == 16_000


def test_resample_preserves_tone():
    # A 440 Hz tone should keep its energy (RMS of a 0.5 sine is ~0.354).
    for from_rate in [48_000, 44_100, 22_050, 8_000]:
        out = resample(sine(440.0, from_rate, 1.0), from_rate, 16_000)
        r = rms(out)
        assert abs(r - 0.354) < 0.02, f"rate {from_rate}: rms {r}"


def test_upsample_interpolates():
    out = resample([0.0, 1.0], 1, 2)
    np.testing.assert_allclose(out, [0.0, 0.5, 1.0, 1.0], rtol=1e-5, atol=1e-5)


def test_silence_detection():
    assert is_silence([])
    assert is_silence(np.zeros(16_000, dtype=np.float32))
    assert is_silence(sine(200.0, 16_000, 0.1))  # too short
    assert not is_silence(sine(200.0, 16_000, 1.0))


def test_wav_roundtrip_stereo_44k(tmp_path: Path):
    path = tmp_path / "test_roundtrip.wav"
    samples = sine(300.0, 44_100, 0.5)

    # Interleaved stereo 16-bit PCM
    interleaved = bytearray()
    for s in samples:
        v = int(s * 32767.0)
        packed = int.to_bytes(v, 2, "little", signed=True)
        interleaved.extend(packed)
        interleaved.extend(packed)

    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44_100)
        w.writeframes(bytes(interleaved))

    out = load_wav(path)
    assert len(out) == 8_000
    assert abs(rms(out) - 0.354) < 0.02


def _write_raw_wav(path: Path, fmt_tag: int, channels: int, rate: int, bits: int, data: bytes) -> None:
    import struct
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", fmt_tag, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def test_load_float32_wav(tmp_path):
    src = np.array(sine(440.0, WHISPER_RATE, 0.5), dtype="<f4")
    p = tmp_path / "f.wav"
    _write_raw_wav(p, 3, 1, WHISPER_RATE, 32, src.tobytes())
    out = load_wav(p)
    assert np.allclose(out, src, atol=1e-6)


def test_load_int32_and_24bit_stereo_wav(tmp_path):
    src = np.array(sine(440.0, WHISPER_RATE, 0.25), dtype=np.float64)
    i32 = (src * 2**31 * 0.999).astype("<i4")
    p32 = tmp_path / "i32.wav"
    _write_raw_wav(p32, 1, 1, WHISPER_RATE, 32, i32.tobytes())
    assert np.allclose(load_wav(p32), src, atol=1e-3)

    i24 = (src * 2**23 * 0.999).astype(np.int32)
    stereo = np.repeat(i24, 2)
    packed = b"".join(int(v).to_bytes(3, "little", signed=True) for v in stereo)
    p24 = tmp_path / "i24.wav"
    _write_raw_wav(p24, 1, 2, WHISPER_RATE, 24, packed)
    assert np.allclose(load_wav(p24), src, atol=1e-3)


def test_load_wav_rejects_non_wav(tmp_path):
    p = tmp_path / "x.wav"
    p.write_bytes(b"not a wav file at all")
    with pytest.raises(ValueError):
        load_wav(p)
