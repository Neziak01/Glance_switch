"""System tray icon and menu (pystray). Menu actions are posted to a queue."""

from __future__ import annotations

import queue
from typing import Callable

from PIL import Image, ImageDraw

from . import APP_NAME
from .config import Settings


def make_icon(active: bool = True, size: int = 64) -> Image.Image:
    """An eye glyph; grey when tracking is paused."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = (79, 140, 255, 255) if active else (140, 140, 150, 255)
    pad = size * 0.06
    d.ellipse((pad, size * 0.22, size - pad, size * 0.78), outline=color, width=max(2, size // 12))
    r = size * 0.17
    c = size / 2
    d.ellipse((c - r, c - r, c + r, c + r), fill=color)
    return img


class Tray:
    def __init__(
        self,
        settings: Settings,
        commands: "queue.Queue[tuple]",
        autostart_state: Callable[[], bool],
    ):
        import pystray

        self._pystray = pystray
        self.settings = settings
        self.commands = commands
        self.autostart_state = autostart_state
        self.icon = pystray.Icon(APP_NAME, make_icon(settings.enabled), APP_NAME, self._menu())

    def _post(self, *cmd) -> Callable:
        return lambda icon, item: self.commands.put(cmd)

    def _menu(self):
        Menu, Item = self._pystray.Menu, self._pystray.MenuItem
        s = self.settings
        cameras = Menu(
            *[
                Item(
                    f"Caméra {i}",
                    self._post("camera", i),
                    checked=lambda item, i=i: s.camera_index == i,
                    radio=True,
                )
                for i in range(4)
            ]
        )
        return Menu(
            Item("Activé", self._post("toggle", "enabled"), checked=lambda i: s.enabled, default=True),
            Item("Calibrer…", self._post("calibrate")),
            Menu.SEPARATOR,
            Item("Double Ctrl", self._post("toggle", "trigger_double_ctrl"), checked=lambda i: s.trigger_double_ctrl),
            Item("Regard prolongé (dwell)", self._post("toggle", "trigger_dwell"), checked=lambda i: s.trigger_dwell),
            Item(
                "Cible",
                Menu(
                    Item("Fenêtre regardée", self._post("target", "window"),
                         checked=lambda i: s.target_mode == "window", radio=True),
                    Item("Écran regardé", self._post("target", "screen"),
                         checked=lambda i: s.target_mode == "screen", radio=True),
                ),
            ),
            Item("Déplacer la souris", self._post("toggle", "move_mouse"), checked=lambda i: s.move_mouse),
            Item("Afficher le point de regard", self._post("toggle", "show_gaze"), checked=lambda i: s.show_gaze),
            Item("Caméra", cameras),
            Menu.SEPARATOR,
            Item("Lancer au démarrage de Windows", self._post("autostart"), checked=lambda i: self.autostart_state()),
            Item("Quitter", self._post("quit")),
        )

    def start(self) -> None:
        self.icon.run_detached()

    def refresh(self, status: str = "") -> None:
        self.icon.icon = make_icon(self.settings.enabled)
        self.icon.title = f"{APP_NAME} — {status}" if status else APP_NAME
        self.icon.update_menu()

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, APP_NAME)
        except Exception:  # noqa: BLE001 - notifications are best-effort
            pass

    def stop(self) -> None:
        self.icon.stop()
