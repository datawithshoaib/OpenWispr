from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from queue import Empty, Queue
from typing import Protocol

import numpy as np

from openwispr import audio
from openwispr.hotkey import Action
from openwispr.output import write_stderr
from openwispr.platform_win import Cue
from openwispr.status import Status

DEBUG_ENV = "OPENWISPR_DEBUG"


def debug_from_environment() -> bool:
    return os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes")


class ChordListener(Protocol):
    def start(self, actions: Queue[Action]) -> None: ...
    def stop(self) -> None: ...


class Microphone(Protocol):
    def start(self) -> None: ...
    def stop(self) -> np.ndarray: ...


class Transcriber(Protocol):
    def transcribe(self, samples: np.ndarray) -> str: ...


class Inserter(Protocol):
    def insert(self, text: str) -> None: ...


class SoundCues(Protocol):
    def play(self, cue: Cue) -> None: ...


class DictationModule:
    """Owns every Dictation: takes Chord actions from its Chord listener,
    records from the microphone, transcribes on a worker thread, inserts each
    Dictation's words exactly once and reports Listening / Transcribing / Idle."""

    def __init__(
        self,
        *,
        chord_listener: ChordListener,
        microphone: Microphone,
        transcriber: Transcriber,
        inserter: Inserter,
        cues: SoundCues,
        debug: bool | None = None,
    ) -> None:
        self._chord_listener = chord_listener
        self._microphone = microphone
        self._transcriber = transcriber
        self._inserter = inserter
        self._cues = cues
        self._debug = debug if debug is not None else debug_from_environment()
        self._actions: Queue[Action] = Queue()
        self._work: Queue[np.ndarray | None] = Queue()
        self._on_status: Callable[[Status], None] = lambda status: None
        self._state = threading.Condition()
        self._recording = False
        self._finishing = False
        self._pending = 0
        self._shutting_down = threading.Event()
        self._insert_lock = threading.Lock()
        self._action_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None

    def start(self, on_status: Callable[[Status], None]) -> None:
        """Starts listening for Chord actions; every status change goes to on_status."""
        self._on_status = on_status
        self._shutting_down.clear()
        with self._state:
            self._finishing = False
        self._worker_thread = threading.Thread(target=self._transcribe_queued, name="dictation-worker", daemon=True)
        self._worker_thread.start()
        self._chord_listener.start(self._actions)
        self._action_thread = threading.Thread(target=self._handle_actions, name="dictation-actions", daemon=True)
        self._action_thread.start()

    def finish(self, timeout: float) -> bool:
        """Stops any recording and waits for outstanding transcriptions to be
        inserted. Returns whether that happened within the timeout. Once
        finishing has begun, Chord Starts are ignored."""
        with self._state:
            self._finishing = True
        self._stop_recording()
        deadline = time.monotonic() + timeout
        with self._state:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._state.wait(remaining)
        return True

    def shut_down(self) -> None:
        """Stops listening for Chord actions and ends the background threads.
        Nothing is inserted after this returns, even if a transcription that
        finish gave up on completes later.

        If finish timed out, the worker thread may still be stuck inside a
        transcription and outlive this call. It is a daemon thread and is
        deliberately not killed (Python cannot interrupt it safely); whatever
        it transcribes is discarded because insertion is refused once
        shutting down has begun."""
        with self._insert_lock:
            self._shutting_down.set()
        self._chord_listener.stop()
        self._join(self._action_thread)
        self._stop_recording()
        self._work.put(None)
        self._join(self._worker_thread)

    def _handle_actions(self) -> None:
        while not self._shutting_down.is_set():
            try:
                action = self._actions.get(timeout=0.2)
            except Empty:
                continue
            if self._shutting_down.is_set():
                return
            self._log(f"chord action {action.name}")
            if action is Action.Start:
                self._start_recording()
            elif action is Action.Stop:
                self._log("Stop: finishing dictation")
                self._stop_recording()
            elif action is Action.Cancel:
                self._cancel_recording()

    # The microphone is started and stopped while holding the state lock, so
    # the recording flag and the microphone always change together: a Start
    # cannot slip in between a Stop (or finish) and the microphone stopping.

    def _start_recording(self) -> None:
        with self._state:
            if self._recording:
                return
            if self._finishing:
                self._log("Start ignored: Quit is finishing")
                return
            try:
                self._microphone.start()
            except Exception as e:
                write_stderr(f"  microphone error: {e}\n")
                return
            self._recording = True
            self._report_status()
        self._play(Cue.Start)
        write_stderr("● listening...\n")

    def _stop_recording(self) -> None:
        with self._state:
            if not self._recording:
                self._report_status()
                return
            self._recording = False
            self._pending += 1
            self._report_status()
            samples = self._stop_microphone()
        self._play(Cue.Stop)
        if samples is None:
            self._settle_one()
        elif audio.is_silence(samples):
            write_stderr("  (too short or silent, ignored)\n")
            self._log_not_inserted("silent recording")
            self._settle_one()
        else:
            self._work.put(samples)

    def _cancel_recording(self) -> None:
        with self._state:
            was_recording = self._recording
            self._recording = False
            if was_recording:
                self._stop_microphone()
            self._report_status()
        if was_recording:
            self._play(Cue.Cancel)
            write_stderr("  cancelled\n")

    def _stop_microphone(self) -> np.ndarray | None:
        """Needs the state lock. Returns the recorded samples, or None if the
        microphone failed."""
        try:
            return self._microphone.stop()
        except Exception as e:
            write_stderr(f"  microphone error: {e}\n")
            return None

    def _play(self, cue: Cue) -> None:
        try:
            self._cues.play(cue)
        except Exception as e:
            write_stderr(f"  sound cue error: {e}\n")

    def _transcribe_queued(self) -> None:
        while True:
            samples = self._work.get()
            if samples is None:
                return
            try:
                self._insert(self._transcribe(samples))
            finally:
                self._settle_one()

    def _transcribe(self, samples: np.ndarray) -> str:
        try:
            secs = len(samples) / audio.WHISPER_RATE
            start = time.time()
            text = self._transcriber.transcribe(samples)
        except Exception as e:
            write_stderr(f"  {e}\n")
            return ""
        if not text:
            write_stderr("  (no speech detected)\n")
        else:
            write_stderr(f"  {secs:.1f}s -> {time.time() - start:.2f}s: {text}\n")
        self._log(f"transcription result: {text}")
        return text

    def _insert(self, text: str) -> None:
        if not text:
            self._log_not_inserted("empty transcript")
            return
        with self._insert_lock:
            if self._shutting_down.is_set():
                self._log_not_inserted("after shut down")
                return
            self._log(f"insertion call: {text!r}")
            try:
                self._inserter.insert(f"{text} ")
            except Exception as e:
                write_stderr(f"  insertion failed: {e}\n")

    def _settle_one(self) -> None:
        with self._state:
            self._pending -= 1
            self._state.notify_all()
            self._report_status()

    def _report_status(self) -> None:
        """The only place status is reported. Call it with the state lock held,
        straight after changing the recording flag or the pending count, so each
        reported status is derived from the state it describes and reports
        arrive in the order the state changed. Recording beats pending
        transcriptions, which beat Idle."""
        if self._recording:
            self._on_status(Status.Listening)
        elif self._pending > 0:
            self._on_status(Status.Transcribing)
        else:
            self._on_status(Status.Idle)

    @staticmethod
    def _join(thread: threading.Thread | None) -> None:
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _log_not_inserted(self, reason: str) -> None:
        self._log(f"transcription ignored ({reason}): no insertion")

    def _log(self, message: str) -> None:
        if self._debug:
            write_stderr(f"[openwispr] {message}\n")
