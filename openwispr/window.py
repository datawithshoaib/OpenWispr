from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from openwispr.desktop_app import Status
from openwispr.wave_indicator import WaveIndicator


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
        factory = indicator_factory if indicator_factory is not None else WaveIndicator
        self._indicator = factory(self._root)

        frame = tk.Frame(self._root, padx=18, pady=14)
        frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(frame, text="Status", font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(frame, textvariable=self._status, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Button(frame, text="Quit", command=self._quit).pack(anchor="e", pady=(12, 0))

        self._root.protocol("WM_DELETE_WINDOW", self._quit)

    def set_quit_handler(self, handler: Callable[[], None]) -> None:
        self._quit_handler = handler

    def show_status(self, status: Status) -> None:
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

    def close(self) -> None:
        self._indicator.close()
        try:
            self._root.destroy()
        except tk.TclError:
            pass

    def _quit(self) -> None:
        if self._quit_handler is not None:
            self._quit_handler()
