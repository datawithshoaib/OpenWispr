from __future__ import annotations

import math
import tkinter as tk

WIDTH = 56
HEIGHT = 26
BARS = 5
FRAME_MS = 40
BOTTOM_MARGIN = 60

TRANSPARENT = "#010102"
PILL_FILL = "#0f0f14"
PILL_OUTLINE = "#0ea5e9"
BAR_FILL = "#38bdf8"


def round_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs: object,
) -> int:
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class WaveIndicator:
    def __init__(self, parent: tk.Misc) -> None:
        self._window = tk.Toplevel(parent)
        self._window.withdraw()
        self._window.overrideredirect(True)
        self._window.resizable(False, False)
        self._window.attributes("-topmost", True)
        try:
            self._window.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            pass

        self._canvas = tk.Canvas(
            self._window,
            width=WIDTH,
            height=HEIGHT,
            bg=TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self._canvas.pack()
        self._frame = 0
        self._after: str | None = None

    def show(self) -> None:
        if self._after is not None:
            return
        screen_w = self._window.winfo_screenwidth()
        screen_h = self._window.winfo_screenheight()
        x = (screen_w - WIDTH) // 2
        y = screen_h - HEIGHT - BOTTOM_MARGIN
        self._window.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        self._window.deiconify()
        self._window.lift()
        self._frame = 0
        self._draw()
        self._schedule()

    def hide(self) -> None:
        if self._after is not None:
            try:
                self._window.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
            self._after = None
        self._window.withdraw()

    def close(self) -> None:
        self.hide()
        try:
            self._window.destroy()
        except tk.TclError:
            pass

    def _schedule(self) -> None:
        self._after = self._window.after(FRAME_MS, self._tick)

    def _tick(self) -> None:
        self._after = None
        self._frame += 1
        self._draw()
        self._schedule()

    def _draw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        round_rect(
            canvas,
            1,
            1,
            WIDTH - 1,
            HEIGHT - 1,
            10,
            fill=PILL_FILL,
            outline=PILL_OUTLINE,
            width=1,
        )
        bar_width = 3
        gap = 3
        start_x = 14
        center_y = HEIGHT // 2
        for index in range(BARS):
            phase = self._frame * 0.075 + index * 0.65
            amplitude = 0.35 + 0.65 * abs(math.sin(phase))
            half_height = 2.0 + amplitude * 4.5
            x = start_x + index * (bar_width + gap)
            round_rect(
                canvas,
                x,
                center_y - half_height,
                x + bar_width,
                center_y + half_height,
                1,
                fill=BAR_FILL,
                outline="",
            )
