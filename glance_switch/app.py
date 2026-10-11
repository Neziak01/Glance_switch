"""Application wiring: tracker, triggers, tray and focus switching.

Threads: the Tk main loop owns all state and every Win32 focus call; the
tracker, input hook and tray threads only post to `self.commands`.
"""

from __future__ import annotations

import logging
import queue
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

from . import APP_NAME, targeting, win32
from .config import Settings, data_dir
from .gaze_model import GazeModel
from .overlay import Calibration, Flash, GazeDot
from .tracker import Tracker
from .tray import Tray
from .triggers import DoubleTapDetector, DwellDetector

log = logging.getLogger(__name__)

POLL_MS = 40
STALE_S = 0.5  # ignore gaze samples older than this


class App:
    def __init__(self) -> None:
        self.settings = Settings.load()
        self.calibration_path: Path = data_dir() / "calibration.json"
        self.commands: "queue.Queue[tuple]" = queue.Queue()
        self.status = "Démarrage"

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP_NAME)

        self.tracker = Tracker(self.settings.camera_index, on_status=self._post_status)
        self.double_tap = DoubleTapDetector(interval=self.settings.double_tap_ms / 1000)
        self.dwell = DwellDetector(dwell=self.settings.dwell_ms / 1000)
        self.hook = win32.InputHook(self._on_key, self._on_mouse_button)
        self.tray = Tray(self.settings, self.commands, lambda: win32.autostart_enabled(APP_NAME))
        self.gaze_dot: Optional[GazeDot] = None
        self.flash = Flash(self.root)
        self.calibration: Optional[Calibration] = None
        self.cursor_memory: dict[int, tuple[float, float]] = {}

    # --- thread-safe entry points -------------------------------------------
    def _post_status(self, msg: str) -> None:
        self.commands.put(("status", msg))

    def _on_key(self, vk: int, is_down: bool) -> None:
        is_ctrl = vk in win32.CTRL_KEYS
        if self.double_tap.event(vk, is_down, time.monotonic(), is_ctrl):
            self.commands.put(("switch",))

    def _on_mouse_button(self) -> None:
        self.double_tap.reset()  # Ctrl+click is not a double tap

    # --- lifecycle -----------------------------------------------------------
    def run(self) -> None:
        model = GazeModel.load(self.calibration_path)
        self.tracker.set_model(model)
        self.tracker.set_paused(not self.settings.enabled)
        self.tracker.start()
        self.hook.start()
        self.tray.start()
        if self.settings.show_gaze:
            self.gaze_dot = GazeDot(self.root)

        if model is None:
            self.tray.notify("Bienvenue ! Calibrons d'abord le suivi du regard.")
            self.root.after(1500, self.start_calibration)
        elif model.layout != [list(m.rect) for m in win32.monitors()]:
            self.tray.notify("La disposition des écrans a changé : recalibrez depuis le menu.")

        self.root.after(POLL_MS, self._poll)
        self.root.mainloop()

    def quit(self) -> None:
        self.hook.stop()
        self.tracker.stop()
        self.tray.stop()
        self.root.quit()

    # --- main loop -----------------------------------------------------------
    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.commands.get_nowait())
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("command failed")

        try:
            self._tick()
        except Exception:  # noqa: BLE001
            log.exception("tick failed")
        self.root.after(POLL_MS, self._poll)

    def _fresh_gaze(self) -> Optional[tuple[float, float]]:
        sample = self.tracker.get_latest()
        if sample is None or sample.gaze is None or time.monotonic() - sample.t > STALE_S:
            return None
        return sample.gaze

    def _tick(self) -> None:
        gaze = self._fresh_gaze() if self.settings.enabled and not self.calibration else None
        if self.gaze_dot is not None:
            self.gaze_dot.move(*(gaze or (None, None)))
        if gaze is None or not self.settings.trigger_dwell:
            self.dwell.reset()
            return
        if win32.mouse_button_down():
            self.dwell.reset()
            return
        target = self._target_at(*gaze)
        hwnd = target.hwnd if target else None
        fired = self.dwell.update(hwnd, time.monotonic(), current=win32.foreground_window())
        if fired is not None and target is not None:
            self._focus(target)

    def _target_at(self, x: float, y: float) -> Optional[win32.Window]:
        return targeting.pick_target(
            win32.windows_z_order(), win32.monitors(), x, y, self.settings.target_mode
        )

    def _focus(self, target: win32.Window) -> None:
        previous = win32.foreground_window()
        if previous == target.hwnd:
            return
        if self.settings.move_mouse and previous and win32.is_window(previous):
            self._remember_cursor(previous)
        if not win32.activate(target.hwnd):
            log.info("could not focus %r", target.title)
            return
        if self.settings.move_mouse:
            win32.set_cursor_pos(*self._cursor_spot(target))
        self.flash.show(target.rect)

    def _remember_cursor(self, hwnd: int) -> None:
        x, y = win32.cursor_pos()
        rect = win32.window_rect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        if w > 0 and h > 0 and targeting.contains(rect, x, y):
            self.cursor_memory[hwnd] = ((x - rect[0]) / w, (y - rect[1]) / h)

    def _cursor_spot(self, target: win32.Window) -> tuple[int, int]:
        """Where the cursor was last left in this window, else its centre."""
        left, top, right, bottom = target.rect
        fx, fy = self.cursor_memory.get(target.hwnd, (0.5, 0.5))
        return int(left + fx * (right - left)), int(top + fy * (bottom - top))

    # --- commands --------------------------------------------------------------
    def _handle(self, cmd: tuple) -> None:
        name, *args = cmd
        s = self.settings
        if name == "switch":
            if not s.enabled or not s.trigger_double_ctrl or self.calibration:
                return
            gaze = self._fresh_gaze()
            if gaze is None:
                return
            target = self._target_at(*gaze)
            if target is not None:
                self._focus(target)
        elif name == "status":
            self.status = args[0]
            self.tray.refresh(self.status)
        elif name == "toggle":
            key = args[0]
            setattr(s, key, not getattr(s, key))
            if key == "enabled":
                self.tracker.set_paused(not s.enabled)
                self.status = "Actif" if s.enabled else "En pause"
            elif key == "show_gaze":
                if s.show_gaze and self.gaze_dot is None:
                    self.gaze_dot = GazeDot(self.root)
                elif not s.show_gaze and self.gaze_dot is not None:
                    self.gaze_dot.destroy()
                    self.gaze_dot = None
            self._save()
        elif name == "target":
            s.target_mode = args[0]
            self._save()
        elif name == "camera":
            s.camera_index = args[0]
            self.tracker.set_camera(args[0])
            self._save()
        elif name == "autostart":
            win32.set_autostart(APP_NAME, not win32.autostart_enabled(APP_NAME))
            self.tray.refresh(self.status)
        elif name == "calibrate":
            self.start_calibration()
        elif name == "quit":
            self.quit()

    def _save(self) -> None:
        self.settings.save()
        self.tray.refresh(self.status)

    # --- calibration ---------------------------------------------------------
    def start_calibration(self) -> None:
        if self.calibration is not None:
            return
        if not self.settings.enabled:
            self.settings.enabled = True
            self.tracker.set_paused(False)
            self._save()
        self.calibration = Calibration(
            self.root,
            self.tracker,
            win32.monitors(),
            self.settings.points_per_screen,
            self._calibration_done,
        )

    def _calibration_done(self, model: Optional[GazeModel], message: str) -> None:
        self.calibration = None
        if model is not None:
            model.save(self.calibration_path)
            self.tracker.set_model(model)
        self.tray.notify(message)
