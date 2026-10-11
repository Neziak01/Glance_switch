import json
import math
from types import SimpleNamespace

import numpy as np
import pytest

from glance_switch import features, targeting
from glance_switch.config import Settings
from glance_switch.gaze_model import GazeModel
from glance_switch.smoothing import OneEuro, PointFilter
from glance_switch.triggers import DoubleTapDetector, DwellDetector


# --- features -----------------------------------------------------------------
def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    m = np.eye(4)
    m[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    return m


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    m = np.eye(4)
    m[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]
    return m


def test_head_angles_yaw_and_pitch():
    yaw, pitch, roll = features.head_angles(rot_y(0.3))
    assert yaw == pytest.approx(0.3)
    assert pitch == pytest.approx(0.0, abs=1e-9)
    assert roll == pytest.approx(0.0, abs=1e-9)
    _, pitch, _ = features.head_angles(rot_x(-0.2))
    assert pitch == pytest.approx(0.2)


def make_landmarks(iris_shift=0.0):
    pts = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]

    def put(i, x, y):
        pts[i] = SimpleNamespace(x=x, y=y)

    # Eye A spans x 0.30..0.40, eye B 0.60..0.70, both at y = 0.40.
    put(33, 0.30, 0.40); put(133, 0.40, 0.40); put(159, 0.35, 0.39); put(145, 0.35, 0.41)
    put(263, 0.70, 0.40); put(362, 0.60, 0.40); put(386, 0.65, 0.39); put(374, 0.65, 0.41)
    put(468, 0.35 + iris_shift, 0.40)
    put(473, 0.65 + iris_shift, 0.40)
    return pts


def test_iris_offset_centre_and_shift():
    h, v = features.iris_offset(make_landmarks(), features.EYE_A, aspect=1.0)
    assert h == pytest.approx(0.5)
    assert v == pytest.approx(0.0, abs=1e-9)
    h2, _ = features.iris_offset(make_landmarks(0.02), features.EYE_A, aspect=1.0)
    assert h2 == pytest.approx(0.7)


def test_extract_shape_and_rejects_bad_input():
    vec = features.extract(make_landmarks(), np.eye(4))
    assert vec.shape == (features.N_FEATURES,)
    assert features.extract(make_landmarks()[:468], np.eye(4)) is None
    assert features.extract(make_landmarks(), None) is None


# --- gaze model -----------------------------------------------------------------
def synthetic(n=200, seed=0):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, features.N_FEATURES)) * 0.1
    # Screen position driven mostly by yaw/pitch, partly by the irises.
    sx = 1920 + 6000 * x[:, 0] + 2000 * (x[:, 6] + x[:, 8]) + 3000 * x[:, 0] ** 2
    sy = 540 + 5000 * x[:, 1] + 1500 * (x[:, 7] + x[:, 9])
    return x, np.column_stack([sx, sy])


def test_gaze_model_fits_and_roundtrips(tmp_path):
    x, y = synthetic()
    model = GazeModel.fit(x, y, alpha=0.01, layout=[[0, 0, 1920, 1080]])
    assert model.error_px < 25
    xt, yt = synthetic(20, seed=1)
    err = np.linalg.norm(model.predict_many(xt) - yt, axis=1).mean()
    assert err < 40

    path = tmp_path / "cal.json"
    model.save(path)
    loaded = GazeModel.load(path)
    assert loaded.layout == [[0, 0, 1920, 1080]]
    assert loaded.predict(xt[0]) == pytest.approx(model.predict(xt[0]))


def test_gaze_model_rejects_bad_files(tmp_path):
    path = tmp_path / "cal.json"
    assert GazeModel.load(path) is None
    path.write_text("{not json")
    assert GazeModel.load(path) is None
    path.write_text(json.dumps({"version": 99}))
    assert GazeModel.load(path) is None
    with pytest.raises(ValueError):
        GazeModel.fit(np.zeros((3, features.N_FEATURES)), np.zeros((3, 2)))


# --- triggers ---------------------------------------------------------------------
def tap(d, t, key=0xA2, hold=0.05):
    a = d.event(key, True, t, True)
    b = d.event(key, False, t + hold, True)
    return a or b


def test_double_tap_fires():
    d = DoubleTapDetector(interval=0.4)
    assert not tap(d, 0.0)
    assert tap(d, 0.2)
    assert not tap(d, 0.4)  # state reset after firing


def test_double_tap_too_slow_or_held_or_interrupted():
    d = DoubleTapDetector(interval=0.4, max_hold=0.3)
    tap(d, 0.0)
    assert not tap(d, 1.0)

    d.reset()
    tap(d, 0.0, hold=0.5)
    assert not tap(d, 0.7)

    d.reset()
    tap(d, 0.0)
    d.event(0x43, True, 0.1, False)  # Ctrl+C style interruption
    d.event(0x43, False, 0.12, False)
    assert not tap(d, 0.2)


def test_double_tap_ignores_autorepeat():
    d = DoubleTapDetector()
    d.event(0xA2, True, 0.0, True)
    d.event(0xA2, True, 0.03, True)
    d.event(0xA2, False, 0.06, True)
    assert tap(d, 0.2)


def test_dwell():
    d = DwellDetector(dwell=0.5, cooldown=0.0)
    assert d.update(1, 0.0, current=2) is None
    assert d.update(1, 0.3, current=2) is None
    assert d.update(1, 0.6, current=2) == 1
    assert d.update(1, 0.9, current=2) is None  # fires once per visit
    assert d.update(2, 1.0, current=1) is None
    assert d.update(2, 2.0, current=2) is None  # already focused
    assert d.update(None, 2.1) is None


# --- targeting -----------------------------------------------------------------
W = SimpleNamespace


def test_pick_target_window_and_screen_modes():
    monitors = [W(rect=(0, 0, 1920, 1080)), W(rect=(1920, 0, 3840, 1080))]
    windows = [
        W(rect=(100, 100, 600, 600), hwnd=1),  # topmost
        W(rect=(0, 0, 1920, 1080), hwnd=2),
        W(rect=(1920, 0, 3840, 1080), hwnd=3),
    ]
    assert targeting.pick_target(windows, monitors, 300, 300).hwnd == 1
    assert targeting.pick_target(windows, monitors, 1000, 900).hwnd == 2
    assert targeting.pick_target(windows, monitors, 3000, 500).hwnd == 3
    # Gaze overshooting the right edge clamps to the right monitor.
    assert targeting.pick_target(windows, monitors, 4500, 500).hwnd == 3
    # Screen mode picks the topmost window mostly on that monitor.
    assert targeting.pick_target(windows, monitors, 1000, 900, mode="screen").hwnd == 1


def test_pick_target_falls_back_when_nothing_under_point():
    monitors = [W(rect=(0, 0, 1920, 1080))]
    windows = [W(rect=(0, 0, 400, 400), hwnd=7)]
    assert targeting.pick_target(windows, monitors, 1500, 900).hwnd == 7
    assert targeting.pick_target([], monitors, 10, 10) is None


def test_calibration_points():
    pts = targeting.calibration_points((1920, 0, 3840, 1080), 9)
    assert len(pts) == 9 and len(set(pts)) == 9
    assert all(1920 <= x < 3840 and 0 <= y < 1080 for x, y in pts)
    assert targeting.calibration_points((0, 0, 100, 100), 5)[0] == (50, 50)


# --- smoothing / config -----------------------------------------------------------
def test_one_euro_converges_and_filters():
    f = OneEuro(min_cutoff=1.0, beta=0.0)
    assert f(0.0, 0.0) == 0.0
    v = 0.0
    for i in range(1, 200):
        v = f(10.0, i / 30)
    assert v == pytest.approx(10.0, abs=0.01)
    p = PointFilter()
    assert p(1, 2, 0.0) == (1, 2)


def test_settings_roundtrip_and_validation(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(camera_index=2, trigger_dwell=True)
    s.save(path)
    assert Settings.load(path) == s
    path.write_text(json.dumps({"target_mode": "bogus", "dwell_ms": 5, "unknown": 1, "move_mouse": "yes"}))
    loaded = Settings.load(path)
    assert loaded.target_mode == "window"
    assert loaded.dwell_ms == 200
    assert loaded.move_mouse is True


def test_rank_targets_prefers_small_front_window_near_gaze():
    monitors = [W(rect=(0, 0, 1920, 1080))]
    front = W(rect=(200, 200, 700, 700), hwnd=1)
    back = W(rect=(0, 0, 1920, 1080), hwnd=2)
    ranked = targeting.rank_targets([front, back], monitors, 450, 450, sigma=150)
    assert ranked[0][0] is front
    assert sum(share for _, share in ranked) == pytest.approx(1.0)
    # Estimate lands just outside the front window: it still gets real weight.
    ranked = targeting.rank_targets([front, back], monitors, 760, 450, sigma=150)
    assert front in [w for w, _ in ranked]


def test_rank_targets_side_by_side_and_empty():
    monitors = [W(rect=(0, 0, 1920, 1080))]
    left = W(rect=(0, 0, 960, 1080), hwnd=1)
    right = W(rect=(960, 0, 1920, 1080), hwnd=2)
    ranked = targeting.rank_targets([left, right], monitors, 1400, 500, sigma=200)
    assert ranked[0][0] is right and ranked[0][1] > 0.9
    assert targeting.rank_targets([], monitors, 10, 10, sigma=100) == []


def test_tracker_recent_gaze_median(monkeypatch):
    from glance_switch import tracker as tracker_mod

    t = tracker_mod.Tracker()
    now = 100.0
    monkeypatch.setattr(tracker_mod.time, "monotonic", lambda: now)
    assert t.recent_gaze() is None
    for i, (x, y) in enumerate([(10, 10), (12, 11), (500, 500), (11, 12)]):
        t._history.append((now - 0.3 + i * 0.05, x, y))
    t._history.appendleft((now - 5, 9999, 9999))  # too old, ignored
    assert t.recent_gaze() == (11.5, 11.5)
