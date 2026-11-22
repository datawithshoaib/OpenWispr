from __future__ import annotations

import sys
import time
from enum import Enum, auto

from openwispr.hotkey import Action, Chord

DEDUP_WINDOW: float = 5.0
FINISH_TIMEOUT: float = 30.0


class Status(Enum):
    Idle = auto()
    Listening = auto()
    Transcribing = auto()
    Finishing = auto()

    @property
    def label(self) -> str:
        return self.name


class DesktopApp:
    def __init__(
        self,
        *,
        window: object,
        engine: object,
        inserter: object,
        instance_guard: object,
        chord: Chord,
    ) -> None:
        self._window = window
        self._engine = engine
        self._inserter = inserter
        self._instance_guard = instance_guard
        self._chord = chord
        self._last_text = ""
        self._last_time = 0.0
        self._launched = False
        self._quitting = False

    def launch(self) -> None:
        if not self._instance_guard.acquire():
            self._window.focus()
            return
        self._launched = True
        self._window.set_quit_handler(self.quit)
        self._window.show_status(Status.Idle)
        self._engine.start_listening(self._handle_action, self._handle_text)

    def run(self) -> None:
        if not self._launched:
            return
        try:
            self._window.run()
        except KeyboardInterrupt:
            pass
        finally:
            self.quit()

    def quit(self) -> None:
        if not self._launched or self._quitting:
            return
        self._quitting = True
        if self._engine.is_busy():
            self._window.show_status(Status.Finishing)
            if not self._engine.finish(timeout=FINISH_TIMEOUT):
                self._write(f"  finishing timed out after {FINISH_TIMEOUT:g}s; quitting\n")
        self._engine.stop_listening()
        self._window.close()
        self._instance_guard.release()
        self._launched = False
        self._quitting = False

    def _handle_action(self, action: Action) -> None:
        if action is Action.Start:
            if self._engine.start_recording():
                self._window.show_status(Status.Listening)
        elif action is Action.Stop:
            self._window.show_status(Status.Transcribing)
            self._engine.stop_recording()
            if not self._engine.is_busy():
                self._window.show_status(Status.Idle)
        elif action is Action.Cancel:
            self._engine.cancel_recording()
            self._window.show_status(Status.Idle)

    def _handle_text(self, text: str) -> None:
        if not self._quitting:
            self._window.show_status(Status.Idle)
        if not text:
            return
        now = time.time()
        if text == self._last_text and (now - self._last_time) < DEDUP_WINDOW:
            if sys.stderr is not None:
                sys.stderr.write("  (duplicate suppressed)\n")
                sys.stderr.flush()
            return
        self._last_text = text
        self._last_time = now
        try:
            self._inserter.insert(f"{text} ")
        except Exception as e:
            if sys.stderr is not None:
                sys.stderr.write(f"  could not insert text: {e}\n")
                sys.stderr.flush()

    @staticmethod
    def _write(message: str) -> None:
        if sys.stderr is not None:
            sys.stderr.write(message)
            sys.stderr.flush()
