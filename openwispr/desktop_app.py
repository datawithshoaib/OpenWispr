from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from openwispr.output import write_stderr
from openwispr.status import Status

FINISH_TIMEOUT: float = 30.0
FOCUS_ATTEMPTS = 50
FOCUS_RETRY_DELAY: float = 0.1


class Window(Protocol):
    def set_quit_handler(self, handler: Callable[[], None]) -> None: ...
    def show_status(self, status: Status) -> None: ...
    def run(self) -> None: ...
    def stay_responsive_until(self, done: threading.Event) -> None: ...
    def close(self) -> None: ...


class DictationModule(Protocol):
    """What the Desktop App needs from openwispr.dictation.DictationModule."""

    def start(self, on_status: Callable[[Status], None]) -> None: ...
    def finish(self, timeout: float) -> bool: ...
    def shut_down(self) -> None: ...


class InstanceGuard(Protocol):
    def acquire(self) -> bool: ...
    def focus_existing(self) -> bool: ...
    def release(self) -> None: ...


class DesktopApp:
    """Launch (one Instance), run, Quit, and showing the Dictation module's
    status in the window."""

    def __init__(
        self,
        *,
        window_factory: Callable[[], Window],
        dictation_module_factory: Callable[[], DictationModule],
        instance_guard: InstanceGuard,
    ) -> None:
        self._window_factory = window_factory
        self._dictation_module_factory = dictation_module_factory
        self._instance_guard = instance_guard
        self._window: Window | None = None
        self._dictation_module: DictationModule | None = None
        self._status_lock = threading.Lock()
        self._launched = False
        self._quitting = False

    def launch(self) -> bool:
        if not self._instance_guard.acquire():
            self._focus_existing_with_retry()
            return False
        self._launched = True
        self._window = self._window_factory()
        self._dictation_module = self._dictation_module_factory()
        self._window.set_quit_handler(self.quit)
        self._window.show_status(Status.Idle)
        self._dictation_module.start(self._show_status)
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
        with self._status_lock:
            if not self._launched or self._quitting:
                return
            self._quitting = True
            self._window.show_status(Status.Finishing)
        finished = threading.Event()
        threading.Thread(target=self._finish_dictation_module, args=(finished,), name="quit-finish", daemon=True).start()
        try:
            self._window.stay_responsive_until(finished)
        finally:
            self._dictation_module.shut_down()
            self._window.close()
            self._instance_guard.release()
            with self._status_lock:
                self._launched = False
                self._quitting = False

    def _finish_dictation_module(self, finished: threading.Event) -> None:
        """Runs off the Tk thread so the window keeps showing Finishing."""
        try:
            if not self._dictation_module.finish(timeout=FINISH_TIMEOUT):
                write_stderr(f"  finishing timed out after {FINISH_TIMEOUT:g}s; quitting\n")
        finally:
            finished.set()

    def _show_status(self, status: Status) -> None:
        with self._status_lock:
            if self._launched and not self._quitting:
                self._window.show_status(status)
