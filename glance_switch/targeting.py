"""Pure geometry: from a gaze point to the monitor and window to focus."""

from __future__ import annotations

from typing import Optional, Protocol, Sequence

Rect = tuple[int, int, int, int]


class HasRect(Protocol):
    rect: Rect


def contains(rect: Rect, x: float, y: float) -> bool:
    return rect[0] <= x < rect[2] and rect[1] <= y < rect[3]


def distance_to(rect: Rect, x: float, y: float) -> float:
    dx = max(rect[0] - x, 0.0, x - (rect[2] - 1))
    dy = max(rect[1] - y, 0.0, y - (rect[3] - 1))
    return (dx * dx + dy * dy) ** 0.5


def overlap_area(a: Rect, b: Rect) -> int:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(w, 0) * max(h, 0)


def nearest_monitor(monitors: Sequence[HasRect], x: float, y: float):
    """Monitor containing the point, else the closest one (gaze overshoots edges)."""
    if not monitors:
        return None
    return min(monitors, key=lambda m: distance_to(m.rect, x, y))


def clamp_to(rect: Rect, x: float, y: float) -> tuple[float, float]:
    return (min(max(x, rect[0]), rect[2] - 1), min(max(y, rect[1]), rect[3] - 1))


def window_at(windows: Sequence[HasRect], x: float, y: float):
    """Topmost window containing the point (`windows` is in z-order)."""
    for w in windows:
        if contains(w.rect, x, y):
            return w
    return None


def top_window_on(windows: Sequence[HasRect], monitor_rect: Rect):
    """Topmost window that sits mostly on the given monitor."""
    for w in windows:
        area = max((w.rect[2] - w.rect[0]) * (w.rect[3] - w.rect[1]), 1)
        if overlap_area(w.rect, monitor_rect) * 2 >= area:
            return w
    return None


def pick_target(
    windows: Sequence[HasRect],
    monitors: Sequence[HasRect],
    x: float,
    y: float,
    mode: str = "window",
) -> Optional[HasRect]:
    monitor = nearest_monitor(monitors, x, y)
    if monitor is None:
        return window_at(windows, x, y)
    cx, cy = clamp_to(monitor.rect, x, y)
    if mode == "window":
        hit = window_at(windows, cx, cy)
        if hit is not None:
            return hit
    return top_window_on(windows, monitor.rect)


def calibration_points(rect: Rect, count: int = 9, margin: float = 0.08) -> list[tuple[int, int]]:
    """Target dots for one monitor: a 3x3 grid, or centre + 4 corners."""
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    xs = [margin, 0.5, 1 - margin]
    ys = [margin, 0.5, 1 - margin]
    if count == 5:
        fracs = [(0.5, 0.5), (xs[0], ys[0]), (xs[2], ys[0]), (xs[2], ys[2]), (xs[0], ys[2])]
    else:
        # Serpentine order keeps eye travel between dots short.
        fracs = [
            (fx, fy)
            for row, fy in enumerate(ys)
            for fx in (xs if row % 2 == 0 else list(reversed(xs)))
        ]
    return [(int(left + fx * w), int(top + fy * h)) for fx, fy in fracs]
