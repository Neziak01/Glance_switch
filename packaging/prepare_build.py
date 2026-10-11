"""Fetch the face model and render the app icon before running PyInstaller."""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from glance_switch import tracker  # noqa: E402
from glance_switch.tray import make_icon  # noqa: E402


def main() -> None:
    models = ROOT / "models"
    models.mkdir(exist_ok=True)
    target = models / tracker.MODEL_NAME
    if not (target.is_file() and tracker._sha256(target) == tracker.MODEL_SHA256):
        shutil.copy(tracker.ensure_model(), target)
    print(f"model: {target}")

    icon = ROOT / "packaging" / "glance_switch.ico"
    make_icon(size=256).save(icon, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (256, 256)])
    print(f"icon: {icon}")


if __name__ == "__main__":
    main()
