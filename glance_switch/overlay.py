"""Tk overlays: calibration screens, gaze dot and switch flash."""

from __future__ import annotations

import logging
import time
import tkinter as tk
from typing import Callable, Optional, Sequence

import numpy as np

from . import targeting, win32
from .gaze_model import GazeModel
from .tracker import Tracker

log = logging.getLogger(__name__)

BG = "#111318"
FG = "#e8eaf0"
ACCENT = "#4f8cff"
KEY = "#ff00fe"  # transparent colour key for click-through overlays

SETTLE_S = 0.7
COLLECT_S = 0.9
MIN_SAMPLES = 8


def _geometry(rect: targeting.Rect) -> str:
    left, top, right, bottom = rect
    return f"{right - left}x{bottom - top}{left:+d}{top:+d}"


class Calibration:
    """Shows dots on every monitor and fits a GazeModel from the samples."""

    def __init__(
        self,
        root: tk.Tk,
        tracker: Tracker,
        monitors: Sequence[win32.Monitor],
        points_per_screen: int,
        on_done: Callable[[Optional[GazeModel], str], None],
    ):
        self.root = root
        self.tracker = tracker
        self.monitors = list(monitors)
        self.on_done = on_done
        self.targets: list[tuple[int, int, int]] = []  # monitor index, x, y
        for i, m in enumerate(self.monitors):
            for x, y in targeting.calibration_points(m.rect, points_per_screen):
                self.targets.append((i, x, y))
        self.windows: list[tk.Toplevel] = []
        self.canvases: list[tk.Canvas] = []
        self.features: list[np.ndarray] = []
        self.labels: list[tuple[int, int]] = []
        self.index = 0
        self.retried = False
        self.started = False
        self.finished = False
        self._job: Optional[str] = None
        self._build()

    # --- window setup ------------------------------------------------------
    def _build(self) -> None:
        for m in self.monitors:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.configure(bg=BG)
            win.geometry(_geometry(m.rect))
            win.attributes("-topmost", True)
            canvas = tk.Canvas(win, bg=BG, highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            win.bind("<Escape>", lambda e: self.cancel())
            win.bind("<space>", lambda e: self._start())
            # Mouse fallback in case the borderless window misses keyboard focus.
            canvas.bind("<Button-1>", lambda e: self._start())
            canvas.bind("<Button-3>", lambda e: self.cancel())
            self.windows.append(win)
            self.canvases.append(canvas)
        primary = next((i for i, m in enumerate(self.monitors) if m.primary), 0)
        self._intro(primary)
        self.root.after(100, lambda: self.windows[primary].focus_force())

    def _intro(self, idx: int) -> None:
        m = self.monitors[idx]
        c = self.canvases[idx]
        cx, cy = m.width // 2, m.height // 2
        c.create_text(cx, cy - 60, text="Calibration", fill=FG, font=("Segoe UI", 28, "bold"))
        c.create_text(
            cx,
            cy + 10,
            text=(
                "Asseyez-vous comme d'habitude et fixez chaque point jusqu'à ce qu'il disparaisse.\n"
                "Vous pouvez bouger la tête et les yeux naturellement."
            ),
            fill=FG,
            font=("Segoe UI", 14),
            justify="center",
        )
        c.create_text(
            cx,
            cy + 80,
            text="Espace ou clic pour commencer  ·  Échap ou clic droit pour annuler",
            fill=ACCENT,
            font=("Segoe UI", 13),
        )
        self._status_item = c.create_text(cx, cy + 130, text="", fill="#ff9b6a", font=("Segoe UI", 12))
        self._status_idx = idx
        self._update_face_status()

    def _update_face_status(self) -> None:
        if self.started or self.finished:
            return
        sample = self.tracker.get_latest()
        if sample is None or time.monotonic() - sample.t > 1.0:
            msg = "Caméra en attente…"
        elif sample.features is None:
            msg = "Visage non détecté — vérifiez la caméra et l'éclairage"
        else:
            msg = "Visage détecté ✓"
        self.canvases[self._status_idx].itemconfigure(self._status_item, text=msg)
        self._job = self.root.after(300, self._update_face_status)

    # --- sequence ----------------------------------------------------------
    def _start(self) -> None:
        if self.started or self.finished:
            return
        self.started = True
        for w in self.windows:
            w.unbind("<space>")
        for c in self.canvases:
            c.delete("all")
            c.configure(cursor="none")
        self._show_point()

    def _show_point(self) -> None:
        if self.finished:
            return
        if self.index >= len(self.targets):
            self._finish()
            return
        mon_idx, x, y = self.targets[self.index]
        m = self.monitors[mon_idx]
        for c in self.canvases:
            c.delete("all")
        canvas = self.canvases[mon_idx]
        lx, ly = x - m.rect[0], y - m.rect[1]
        start = time.monotonic()
        progress = f"{self.index + 1} / {len(self.targets)}"
        canvas.create_text(m.width // 2, m.height - 30, text=progress, fill="#6b7280", font=("Segoe UI", 11))
        ring = canvas.create_oval(0, 0, 0, 0, outline=ACCENT, width=3)
        dot = canvas.create_oval(lx - 5, ly - 5, lx + 5, ly + 5, fill=FG, outline="")
        collected: list[np.ndarray] = []
        last_t = [0.0]

        def tick() -> None:
            if self.finished:
                return
            elapsed = time.monotonic() - start
            # Ring shrinks during the settle phase to pull the eyes in.
            r = 6 + max(0.0, 1 - elapsed / SETTLE_S) * 34
            canvas.coords(ring, lx - r, ly - r, lx + r, ly + r)
            if elapsed >= SETTLE_S:
                canvas.itemconfigure(dot, fill=ACCENT)
                sample = self.tracker.get_latest()
                if sample and sample.features is not None and sample.t > last_t[0]:
                    last_t[0] = sample.t
                    collected.append(sample.features)
            if elapsed >= SETTLE_S + COLLECT_S:
                self._point_done(collected, x, y)
                return
            self._job = self.root.after(16, tick)

        tick()

    def _point_done(self, collected: list[np.ndarray], x: int, y: int) -> None:
        if len(collected) < MIN_SAMPLES and not self.retried:
            self.retried = True  # one retry per point when the face was lost
            self._show_point()
            return
        self.retried = False
        if len(collected) >= MIN_SAMPLES // 2:
            arr = np.asarray(collected)
            med = np.median(arr, axis=0)
            mad = np.median(np.abs(arr - med), axis=0) + 1e-6
            keep = np.all(np.abs(arr - med) <= 4 * mad * 1.4826, axis=1)
            for vec in arr[keep]:
                self.features.append(vec)
                self.labels.append((x, y))
        self.index += 1
        self._show_point()

    def _finish(self) -> None:
        self.finished = True
        points = len(set(self.labels))
        if points < max(4, len(self.targets) // 2):
            self._close()
            self.on_done(None, "Calibration échouée : visage non détecté sur trop de points.")
            return
        layout = [list(m.rect) for m in self.monitors]
        model = GazeModel.fit(self.features, self.labels, alpha=1.0, layout=layout)
        self._close()
        self.on_done(model, f"Calibration terminée — erreur moyenne ≈ {model.error_px:.0f} px.")

    def cancel(self) -> None:
        if self.finished:
            return
        self.finished = True
        self._close()
        self.on_done(None, "Calibration annulée.")

    def _close(self) -> None:
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except tk.TclError:
                pass
        for w in self.windows:
            w.destroy()
        self.windows.clear()


class _ClickThrough(tk.Toplevel):
    def __init__(self, root: tk.Tk, width: int, height: int):
        super().__init__(root)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-transparentcolor", KEY)
        self.configure(bg=KEY)
        self.canvas = tk.Canvas(self, bg=KEY, highlightthickness=0, width=width, height=height)
        self.canvas.pack(fill="both", expand=True)
        self.update_idletasks()
        win32.make_click_through(win32.toplevel_hwnd(self))


class GazeDot(_ClickThrough):
    SIZE = 28

    def __init__(self, root: tk.Tk):
        super().__init__(root, self.SIZE, self.SIZE)
        s = self.SIZE
        self.canvas.create_oval(3, 3, s - 3, s - 3, outline=ACCENT, width=3)
        self.canvas.create_oval(s / 2 - 3, s / 2 - 3, s / 2 + 3, s / 2 + 3, fill=ACCENT, outline="")
        self.withdraw()

    def move(self, x: Optional[float], y: Optional[float]) -> None:
        if x is None or y is None:
            self.withdraw()
            return
        h = self.SIZE // 2
        self.geometry(f"{self.SIZE}x{self.SIZE}{int(x) - h:+d}{int(y) - h:+d}")
        self.deiconify()
        self.lift()


class Flash:
    """Briefly outlines the window that just received focus."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self._win: Optional[_ClickThrough] = None

    def show(self, rect: targeting.Rect, duration_ms: int = 280) -> None:
        self.hide()
        left, top, right, bottom = rect
        w, h = right - left, bottom - top
        win = _ClickThrough(self.root, w, h)
        win.geometry(_geometry(rect))
        win.canvas.create_rectangle(2, 2, w - 2, h - 2, outline=ACCENT, width=4)
        self._win = win
        self.root.after(duration_ms, self.hide)

    def hide(self) -> None:
        if self._win is not None:
            self._win.destroy()
            self._win = None
