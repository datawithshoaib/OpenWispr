from __future__ import annotations

import sys
import time

from openwispr.hotkey import Action, Chord

DEDUP_WINDOW: float = 5.0


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

    def _handle_action(self, action: Action) -> None:
        if action is Action.Start:
            if self._engine.start_recording():
                self._window.show_listening()
        elif action is Action.Stop:
            self._engine.stop_recording()
            self._window.show_ready(self._chord.label)
        elif action is Action.Cancel:
            self._engine.cancel_recording()
            self._window.show_ready(self._chord.label)

    def _handle_text(self, text: str) -> None:
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
