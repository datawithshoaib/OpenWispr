from __future__ import annotations

import queue
import threading
import tkinter as tk
from collections.abc import Callable

from openwispr.status import Status
from openwispr.wave_indicator import WaveIndicator

STATUS_POLL_MS = 30
"""How often the running event loop applies statuses queued by other threads."""
RESPONSIVE_WAIT_SECONDS = 0.03
"""How long stay_responsive_until waits between event loop passes."""


class TkWindow:
    def __init__(
        self,
        indicator_factory: Callable[[tk.Misc], object] | None = None,
    ) -> None:
        self._root = tk.Tk()
        self._root.title("OpenWispr")
        self._root.geometry("200x100")
        self._root.resizable(False, False)
        self._status = tk.StringVar(value=Status.Idle.label)
        self._quit_handler: Callable[[], None] | None = None
        self._pending_statuses: queue.SimpleQueue[Status] = queue.SimpleQueue()
        self._closed = False
        factory = indicator_factory if indicator_factory is not None else WaveIndicator
        self._indicator = factory(self._root)

        frame = tk.Frame(self._root, padx=18, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Status", font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(frame, textvariable=self._status, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Button(frame, text="Quit", command=self._quit).pack(anchor="e", pady=(12, 0))

        self._root.protocol("WM_DELETE_WINDOW", self._quit)
        self._root.after(STATUS_POLL_MS, self._apply_pending_statuses)

    def set_quit_handler(self, handler: Callable[[], None]) -> None:
        self._quit_handler = handler

    def show_status(self, status: Status) -> None:
        """Safe from any thread: the status is applied later on the Tk thread."""
        self._pending_statuses.put(status)

    def _apply_pending_statuses(self) -> None:
        if self._closed:
            return
        while True:
            try:
                status = self._pending_statuses.get_nowait()
            except queue.Empty:
                break
            self._apply_status(status)
        self._root.after(STATUS_POLL_MS, self._apply_pending_statuses)

    def _apply_status(self, status: Status) -> None:
        self._status.set(status.label)
        if status is Status.Listening:
            self._indicator.show()
        else:
            self._indicator.hide()

    def focus(self) -> None:
        try:
            self._root.deiconify()
            self._root.lift()
            self._root.focus_force()
        except tk.TclError:
            pass

    def run(self) -> None:
        self._root.mainloop()

    def stay_responsive_until(self, done: threading.Event) -> None:
        """Keeps the Tk event loop running (statuses applied, window redrawn)
        until done is set. Call on the Tk thread."""
        while not done.is_set():
            try:
                self._root.update()
            except tk.TclError:
                return
            done.wait(RESPONSIVE_WAIT_SECONDS)

    def close(self) -> None:
        self._closed = True
        self._indicator.close()
        try:
            self._root.destroy()
        except tk.TclError:
            pass

    def _quit(self) -> None:
        if self._quit_handler is not None:
            self._quit_handler()
