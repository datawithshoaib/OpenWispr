from __future__ import annotations

import os
import sys

from openwispr.hotkey import Action, Chord

DEBUG_ENV = "OPENWISPR_DEBUG"


class DesktopApp:
    def __init__(
        self,
        *,
        window: object,
        engine: object,
        inserter: object,
        instance_guard: object,
        chord: Chord,
        debug: bool | None = None,
    ) -> None:
        self._debug = debug if debug is not None else os.environ.get(DEBUG_ENV, "").strip().lower() in ("1", "true", "yes")
        self._window = window
        self._engine = engine
        self._inserter = inserter
        self._instance_guard = instance_guard
        self._chord = chord
        self._dictation_active = False
        self._dictation_pending = False
        self._inserted_for_dictation = False
        self._launched = False

    def launch(self) -> None:
        if not self._instance_guard.acquire():
            self._window.focus()
            return
        self._launched = True
        self._window.show_ready(self._chord.label)
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
        if not self._launched:
            return
        self._launched = False
        self._engine.stop_listening()
        self._window.close()
        self._instance_guard.release()

    def _log(self, message: str) -> None:
        if self._debug and sys.stderr is not None:
            sys.stderr.write(f"[openwispr] {message}\n")
            sys.stderr.flush()

    def _handle_action(self, action: Action) -> None:
        self._log(f"chord action {action.name}")
        if action is Action.Start:
            if self._engine.start_recording():
                self._dictation_active = True
                self._dictation_pending = False
                self._inserted_for_dictation = False
                self._window.show_listening()
        elif action is Action.Stop:
            self._log("Stop: finishing dictation")
            if self._dictation_active:
                self._dictation_active = False
                self._dictation_pending = True
            self._engine.stop_recording()
            self._window.show_ready(self._chord.label)
        elif action is Action.Cancel:
            self._dictation_active = False
            self._dictation_pending = False
            self._engine.cancel_recording()
            self._window.show_ready(self._chord.label)

    def _handle_text(self, text: str) -> None:
        self._log(f"transcription result: {text}")
        if not text or not self._dictation_pending or self._inserted_for_dictation:
            self._log("transcription ignored: no insertion")
            return
        self._dictation_pending = False
        self._inserted_for_dictation = True
        self._log(f"insertion call: {text!r}")
        try:
            self._inserter.insert(f"{text} ")
        except Exception as e:
            self._log(f"insertion failed: {e}")
