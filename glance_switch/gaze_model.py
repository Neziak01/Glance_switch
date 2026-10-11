"""Calibrated mapping from gaze features to virtual-desktop pixels.

A ridge regression on standardised features plus a few quadratic terms.
Small, fast, and robust with the ~10-50 calibration targets we collect.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from .features import N_FEATURES

# Indices into the feature vector used for quadratic terms.
_YAW, _PITCH = 0, 1
_IRIS_H = (6, 8)
_IRIS_V = (7, 9)


def _expand(z: np.ndarray) -> np.ndarray:
    """Standardised features -> design matrix with bias and quadratic terms."""
    z = np.atleast_2d(z)
    iris_h = z[:, _IRIS_H].mean(axis=1)
    iris_v = z[:, _IRIS_V].mean(axis=1)
    extra = np.column_stack(
        [
            z[:, _YAW] ** 2,
            z[:, _PITCH] ** 2,
            z[:, _YAW] * z[:, _PITCH],
            iris_h**2,
            iris_v**2,
            z[:, _YAW] * iris_h,
            z[:, _PITCH] * iris_v,
        ]
    )
    return np.column_stack([np.ones(len(z)), z, extra])


@dataclass
class GazeModel:
    mean: np.ndarray
    std: np.ndarray
    weights: np.ndarray  # (n_terms, 2)
    layout: list = field(default_factory=list)  # monitor rects at calibration
    error_px: float = 0.0

    @classmethod
    def fit(
        cls,
        features: Sequence[np.ndarray],
        targets: Sequence[Sequence[float]],
        alpha: float = 1.0,
        layout: Optional[list] = None,
    ) -> "GazeModel":
        x = np.asarray(features, dtype=float)
        y = np.asarray(targets, dtype=float)
        if x.ndim != 2 or x.shape[1] != N_FEATURES:
            raise ValueError(f"expected (n, {N_FEATURES}) features, got {x.shape}")
        if len(x) != len(y) or len(x) < 4:
            raise ValueError("need at least 4 matching samples")
        mean = x.mean(axis=0)
        std = x.std(axis=0)
        std[std < 1e-9] = 1.0
        design = _expand((x - mean) / std)
        reg = alpha * np.eye(design.shape[1])
        reg[0, 0] = 0.0  # do not shrink the bias
        weights = np.linalg.solve(design.T @ design + reg, design.T @ y)
        model = cls(mean=mean, std=std, weights=weights, layout=list(layout or []))
        pred = model.predict_many(x)
        model.error_px = float(np.mean(np.linalg.norm(pred - y, axis=1)))
        return model

    def predict_many(self, features: np.ndarray) -> np.ndarray:
        z = (np.atleast_2d(features) - self.mean) / self.std
        return _expand(z) @ self.weights

    def predict(self, features: np.ndarray) -> tuple[float, float]:
        x, y = self.predict_many(features)[0]
        return float(x), float(y)

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "mean": self.mean.tolist(),
            "std": self.std.tolist(),
            "weights": self.weights.tolist(),
            "layout": self.layout,
            "error_px": self.error_px,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GazeModel":
        if data.get("version") != 1:
            raise ValueError("unsupported calibration version")
        model = cls(
            mean=np.asarray(data["mean"], dtype=float),
            std=np.asarray(data["std"], dtype=float),
            weights=np.asarray(data["weights"], dtype=float),
            layout=data.get("layout", []),
            error_px=float(data.get("error_px", 0.0)),
        )
        if model.mean.shape != (N_FEATURES,):
            raise ValueError("calibration does not match feature layout")
        return model

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Optional["GazeModel"]:
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            return None
