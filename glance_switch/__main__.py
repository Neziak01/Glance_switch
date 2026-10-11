"""Entry point: `python -m glance_switch` or the packaged GlanceSwitch.exe."""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__
from .config import data_dir


def _setup_logging(verbose: bool) -> None:
    handlers: list[logging.Handler] = [
        RotatingFileHandler(data_dir() / "glance_switch.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    ]
    if sys.stderr is not None:  # None under pythonw / windowed exe
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def selftest() -> int:
    """Headless check used by CI and the installer: model loads and runs."""
    import numpy as np

    from .tracker import Tracker

    tracker = Tracker()
    landmarker = tracker._create_landmarker()
    import mediapipe as mp

    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.zeros((480, 640, 3), np.uint8))
    result = landmarker.detect_for_video(image, 1)
    landmarker.close()
    print(f"{APP_NAME} {__version__}: face model OK ({len(result.face_landmarks)} faces on blank frame)")
    if sys.platform == "win32":
        from . import win32

        mons = win32.monitors()
        wins = win32.windows_z_order()
        print(f"monitors: {len(mons)}, switchable windows: {len(wins)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    parser.add_argument("--selftest", action="store_true", help="check the face model and exit")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    if args.selftest:
        return selftest()

    if sys.platform != "win32":
        print("GlanceSwitch for Windows only runs on Windows.", file=sys.stderr)
        return 1

    from . import win32

    if not win32.acquire_single_instance(APP_NAME):
        win32.message_box("GlanceSwitch est déjà lancé (icône dans la zone de notification).", APP_NAME)
        return 0
    win32.enable_dpi_awareness()

    from .app import App

    try:
        App().run()
    except Exception as exc:  # noqa: BLE001 - last-resort report for a windowed app
        logging.getLogger(__name__).exception("fatal error")
        win32.message_box(f"Erreur fatale : {exc}\n\nJournal : {data_dir() / 'glance_switch.log'}", APP_NAME, error=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
