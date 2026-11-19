"""
Speech-to-text with a local Whisper model (whisper.cpp via pywhispercpp).
"""

from __future__ import annotations

import math
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
import numpy as np
from pywhispercpp.model import Model


@contextmanager
def silence_c_stderr() -> Iterator[None]:
    """Silences C-level stderr (whisper.cpp and VAD logs) during inference."""
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        saved_fd = os.dup(2)
        os.dup2(devnull_fd, 2)
    except OSError:
        # If low-level file descriptor redirection fails, proceed without it.
        # (Only the setup is guarded: errors raised by the body must propagate.)
        yield
        return
    try:
        yield
    finally:
        os.dup2(saved_fd, 2)
        os.close(saved_fd)
        os.close(devnull_fd)


def max_tokens_for(samples: int) -> int:
    """
    An upper bound on tokens a clip can produce: even very fast speech is under
    ~5 tokens per second, so this leaves generous headroom.
    """
    return math.ceil((samples / 16_000.0) * 8.0) + 16


def audio_ctx_for(samples: int) -> int | None:
    """
    Whisper's encoder always processes a 30 s window (1500 frames), even for a
    3 s clip. Shrinking the window to fit the audio (plus a small margin) makes
    short dictations 5-8x faster with the same transcript.
    """
    FULL: int = 1500
    MARGIN: int = 64
    frames = math.ceil((samples / 16_000.0 / 30.0) * FULL) + MARGIN
    return frames if frames < FULL else None


def collapse_repeated_phrases(words: list[str]) -> list[str]:
    """
    Detects repeated multi-word phrases and collapses them to a single occurrence.
    E.g. ["Thank", "you.", "Thank", "you.", "Thank", "you."] -> ["Thank", "you."]
    """
    if len(words) < 4:
        return list(words)
    result = list(words)
    max_phrase_len = min(len(result) // 2, 12)
    for phrase_len in range(2, max_phrase_len + 1):
        collapsed: list[str] = []
        i = 0
        while i < len(result):
            if i + phrase_len * 2 <= len(result):
                phrase = result[i : i + phrase_len]
                reps = 1
                j = i + phrase_len
                while j + phrase_len <= len(result):
                    candidate = result[j : j + phrase_len]
                    if [w.lower() for w in phrase] == [w.lower() for w in candidate]:
                        reps += 1
                        j += phrase_len
                    else:
                        break
                if reps >= 3:
                    collapsed.extend(phrase)
                    i = j
                    continue
            collapsed.append(result[i])
            i += 1
        result = collapsed
    return result


def clean_text(raw: str) -> str:
    """
    Removes Whisper's non-speech annotations (e.g. `[BLANK_AUDIO]`, `(music)`,
    `*laughs*`) and normalizes whitespace.
    """
    out: list[str] = []
    closing: str | None = None
    for c in raw:
        if closing is not None:
            if c == closing:
                closing = None
        else:
            if c == "[":
                closing = "]"
            elif c == "(":
                closing = ")"
            elif c == "*":
                closing = "*"
            else:
                out.append(c)

    filtered = "".join(out)
    words = filtered.split()
    kept: list[str] = []
    i = 0
    while i < len(words):
        target = words[i].lower()
        run = 0
        while i + run < len(words) and words[i + run].lower() == target:
            run += 1
        keep_count = 1 if run > 3 else run
        kept.extend([words[i]] * keep_count)
        i += run

    text = " ".join(collapse_repeated_phrases(kept))
    if not any(c.isalnum() for c in text):
        return ""
    return text


class Transcriber:
    """
    Wraps local Whisper model for fast dictation.
    Reuses model buffers across dictations.
    """

    def __init__(
        self,
        model: Model,
        vad_model_path: str | Path | None = None,
        language: str | None = None,
        threads: int = 4,
    ) -> None:
        self.model = model
        self.vad_model_path = str(vad_model_path) if vad_model_path else None
        self.language = language
        self.threads = threads

    @classmethod
    def load(
        cls,
        model_path: str | Path,
        vad_model_path: str | Path | None = None,
        language: str | None = None,
        threads: int = 4,
    ) -> Transcriber:
        model = Model(str(model_path), redirect_whispercpp_logs_to=os.devnull)
        return cls(
            model=model,
            vad_model_path=vad_model_path,
            language=language,
            threads=threads,
        )

    def transcribe(self, audio: np.ndarray | list[float]) -> str:
        arr = np.asarray(audio, dtype=np.float32)
        if len(arr) == 0:
            return ""

        # whisper.cpp needs at least ~1 s of audio; pad short clips with silence.
        min_samples = 16_000 + 1_600
        if len(arr) < min_samples:
            arr = np.pad(arr, (0, min_samples - len(arr)))

        params: dict = {
            "n_threads": self.threads,
            "language": self.language or "auto",
            "translate": False,
            "no_context": True,
            "no_timestamps": True,
            "suppress_blank": True,
            "suppress_nst": True,
            "print_special": False,
            "print_progress": False,
            "print_realtime": False,
            "print_timestamps": False,
            "temperature_inc": 0.0,
        }

        actx = audio_ctx_for(len(arr))
        if actx is not None:
            params["audio_ctx"] = actx
            params["max_tokens"] = max_tokens_for(len(arr))

        if self.vad_model_path and Path(self.vad_model_path).is_file():
            params["vad"] = True
            params["vad_model_path"] = self.vad_model_path

        with silence_c_stderr():
            segments = self.model.transcribe(arr, **params)
        raw_text = "".join(seg.text for seg in segments)
        return clean_text(raw_text)
