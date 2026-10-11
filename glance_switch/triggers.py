"""Platform-independent trigger logic: double-tap Ctrl and dwell."""

from __future__ import annotations

from typing import Hashable, Optional


class DoubleTapDetector:
    """Detects two quick, clean taps of the same modifier key.

    Feed every key event with `event(key, is_down, t)`. A tap is a press and
    release with no other key in between, held no longer than `max_hold`.
    Two taps less than `interval` apart fire. Any other key cancels, so
    shortcuts such as Ctrl+C never trigger a switch.
    """

    def __init__(self, interval: float = 0.4, max_hold: float = 0.3):
        self.interval = interval
        self.max_hold = max_hold
        self._down_at: Optional[float] = None
        self._down_key: Optional[Hashable] = None
        self._last_tap_at: Optional[float] = None

    def reset(self) -> None:
        self._down_at = None
        self._down_key = None
        self._last_tap_at = None

    def event(self, key: Hashable, is_down: bool, t: float, is_target: bool) -> bool:
        """Returns True when a double tap completes on this event."""
        if not is_target:
            if is_down:
                self.reset()
            return False

        if is_down:
            if self._down_key is not None:
                return False  # auto-repeat while held
            self._down_key, self._down_at = key, t
            return False

        if self._down_key is None or self._down_at is None:
            return False
        held = t - self._down_at
        self._down_key, self._down_at = None, None
        if held > self.max_hold:
            self._last_tap_at = None
            return False

        if (
            self._last_tap_at is not None
            and t - self._last_tap_at <= self.interval
        ):
            self._last_tap_at = None
            return True
        self._last_tap_at = t
        return False


class DwellDetector:
    """Fires once the gaze has rested on the same target for `dwell` seconds.

    Targets are opaque hashables (window handles). `current` is the target
    that already has focus; dwelling on it does nothing. After firing, the
    same target will not fire again until the gaze leaves it.
    """

    def __init__(self, dwell: float = 0.7, cooldown: float = 0.8):
        self.dwell = dwell
        self.cooldown = cooldown
        self._target: Optional[Hashable] = None
        self._since = 0.0
        self._fired = False
        self._last_fire = -1e9

    def reset(self) -> None:
        self._target = None
        self._fired = False

    def update(
        self, target: Optional[Hashable], t: float, current: Optional[Hashable] = None
    ) -> Optional[Hashable]:
        if target != self._target:
            self._target = target
            self._since = t
            self._fired = False
            return None
        if target is None or self._fired or target == current:
            return None
        if t - self._since >= self.dwell and t - self._last_fire >= self.cooldown:
            self._fired = True
            self._last_fire = t
            return target
        return None
