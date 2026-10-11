"""User settings and on-disk locations."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import APP_NAME


def data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.join(Path.home(), ".config")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def resource_dir() -> Path:
    """Folder holding bundled resources (PyInstaller-aware)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    enabled: bool = True
    camera_index: int = 0
    trigger_double_ctrl: bool = True
    trigger_dwell: bool = False
    dwell_ms: int = 700
    double_tap_ms: int = 400
    # "window": focus the window under the gaze point.
    # "screen": focus the top window of the monitor being looked at.
    target_mode: str = "window"
    move_mouse: bool = True
    show_gaze: bool = False
    points_per_screen: int = 9

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or data_dir() / "settings.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for key, value in raw.items():
            f = known.get(key)
            if f is None:
                continue
            default = getattr(cls(), key)
            if isinstance(value, type(default)):
                kwargs[key] = value
        settings = cls(**kwargs)
        if settings.target_mode not in ("window", "screen"):
            settings.target_mode = "window"
        if settings.points_per_screen not in (5, 9):
            settings.points_per_screen = 9
        settings.dwell_ms = min(max(settings.dwell_ms, 200), 3000)
        settings.double_tap_ms = min(max(settings.double_tap_ms, 150), 1000)
        return settings

    def save(self, path: Path | None = None) -> None:
        path = path or data_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
