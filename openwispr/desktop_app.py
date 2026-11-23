from __future__ import annotations

import os
import time
from collections.abc import Callable
from enum import Enum, auto

from openwispr.config import DesktopConfig
from openwispr.dictation import Dictation
from openwispr.hotkey import Action
from openwispr.output import write_stderr

DEBUG_ENV = "OPENWISPR_DEBUG"
FINISH_TIMEOUT: float = 30.0
FOCUS_ATTEMPTS = 50
FOCUS_RETRY_DELAY: float = 0.1


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
        window: object | None = None,
        engine: object | None = None,
        inserter: object,
        instance_guard: object,
        config: DesktopConfig,
        window_factory: Callable[[], object] | None = None,
        engine_factory: Callable[[], object] | None = None,
    ) -> None:
        self._config = config
        self._debug = config.debug if config.debug is not None else os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes")
        self._window_factory = window_factory if window_factory is not None else lambda: window
        self._engine_factory = engine_factory if engine_factory is not None else lambda: engine
        self._window: object | None = None
        self._engine: object | None = None
        self._inserter = inserter
        self._instance_guard = instance_guard
        self._next_dictation_id = 0
        self._active_dictation: Dictation | None = None
        self._pending_dictations: set[Dictation] = set()
        self._seen_dictations: set[Dictation] = set()
        self._launched = False
        self._quitting = False

    def launch(self) -> bool:
        if not self._instance_guard.acquire():
            self._focus_existing_with_retry()
            return False
        self._launched = True
        self._window = self._window_factory()
        self._engine = self._engine_factory()
        self._window.set_quit_handler(self.quit)
        self._window.show_status(Status.Idle)
        self._engine.start_listening(self._handle_action, self._handle_text)
        return True

    def _focus_existing_with_retry(self) -> None:
        for _ in range(FOCUS_ATTEMPTS):
            if self._instance_guard.focus_existing():
                return
            time.sleep(FOCUS_RETRY_DELAY)

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
        if self._active_dictation is not None:
            self._pending_dictations.add(self._active_dictation)
            self._active_dictation = None
        if self._engine.is_busy():
            self._window.show_status(Status.Finishing)
            if not self._engine.finish(timeout=FINISH_TIMEOUT):
                self._write(f"  finishing timed out after {FINISH_TIMEOUT:g}s; quitting\n")
        self._engine.stop_listening()
        self._window.close()
        self._instance_guard.release()
        self._launched = False
        self._quitting = False

    def _log(self, message: str) -> None:
        if self._debug:
            write_stderr(f"[openwispr] {message}\n")

    def _handle_action(self, action: Action) -> None:
        self._log(f"chord action {action.name}")
        if action is Action.Start:
            self._next_dictation_id += 1
            dictation = Dictation(self._next_dictation_id)
            if self._engine.start_recording(dictation):
                self._active_dictation = dictation
                self._window.show_status(Status.Listening)
        elif action is Action.Stop:
            self._log("Stop: finishing dictation")
            if self._active_dictation is not None:
                self._pending_dictations.add(self._active_dictation)
                self._active_dictation = None
            self._window.show_status(Status.Transcribing)
            self._engine.stop_recording()
            if not self._engine.is_busy():
                self._show_settled_status()
        elif action is Action.Cancel:
            if self._active_dictation is not None:
                self._seen_dictations.add(self._active_dictation)
                self._active_dictation = None
            self._engine.cancel_recording()
            self._show_settled_status()

    def _handle_text(self, text: str, dictation: Dictation) -> None:
        self._log(f"transcription result: {text}")
        if dictation not in self._pending_dictations or dictation in self._seen_dictations:
            self._log("transcription ignored: no insertion")
            return
        self._pending_dictations.discard(dictation)
        self._seen_dictations.add(dictation)
        if not self._quitting:
            self._show_settled_status()
        if not text:
            return
        self._log(f"insertion call: {text!r}")
        try:
            self._inserter.insert(f"{text} ")
        except Exception as e:
            self._log(f"insertion failed: {e}")

    def _show_settled_status(self) -> None:
        if self._active_dictation is not None:
            self._window.show_status(Status.Listening)
        elif self._pending_dictations:
            self._window.show_status(Status.Transcribing)
        else:
            self._window.show_status(Status.Idle)

    @staticmethod
    def _write(message: str) -> None:
        write_stderr(message)
