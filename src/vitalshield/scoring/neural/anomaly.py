"""
Layer 5 — Neurosymbolic Acuity Scorer: Neural anomaly detector.

Uses scikit-learn Isolation Forest as the default anomaly detector.
It is trained on the recent vital sign vector and outputs an anomaly score.
No pre-training required — the Isolation Forest operates unsupervised.

For upgrade to PyTorch VAE, swap out the `detect_anomaly` function.
"""

from __future__ import annotations

import logging

import numpy as np
from sklearn.ensemble import IsolationForest

from vitalshield.temporal.models import TemporalProfile

log = logging.getLogger(__name__)

# LOINC codes used as features for the anomaly detection model
_FEATURE_CODES = [
    "8867-4",   # HR
    "8480-6",   # SBP
    "8310-5",   # Temp
    "9279-1",   # RR
    "59408-5",  # SpO2
]
_FEATURE_NAMES = ["HR", "SBP", "Temp", "RR", "SpO2"]


def detect_anomaly(profile: TemporalProfile) -> tuple[float, list[str]]:
    """
    Run anomaly detection across the 72-hour vital trajectory.

    Returns:
        (anomaly_score_0_to_30, list_of_flagged_vitals)
    """
    all_series = {**profile.vitals, **profile.labs}

    # Build feature matrix: one row per timestep, one column per vital
    # Common timestamps via the longest series
    longest_code = max(
        _FEATURE_CODES,
        key=lambda c: len(all_series[c].points) if c in all_series else 0,
        default=None,
    )
    if longest_code is None:
        return 0.0, []

    base_series = all_series.get(longest_code)
    if not base_series or len(base_series.points) < 3:
        return 0.0, []

    # Build feature matrix
    rows = []
    for point in base_series.points:
        row = []
        for code in _FEATURE_CODES:
            series = all_series.get(code)
            if series and series.points:
                # Find closest value
                closest = min(
                    series.points,
                    key=lambda p: abs((p.timestamp - point.timestamp).total_seconds()),
                )
                row.append(closest.value)
            else:
                row.append(0.0)
        rows.append(row)

    if len(rows) < 4:
        return 0.0, []

    X = np.array(rows)

    # Normalize
    means = X.mean(axis=0)
    stds = X.std(axis=0)
    stds[stds == 0] = 1.0
    X_norm = (X - means) / stds

    # Isolation Forest — contamination=0.1 means we expect ~10% outliers
    try:
        iso = IsolationForest(
            n_estimators=100,
            contamination=0.1,
            random_state=42,
        )
        iso.fit(X_norm)
        scores = iso.decision_function(X_norm)  # negative = more anomalous
        # The last point is the most important — use weighted average
        weights = np.linspace(0.5, 1.5, len(scores))
        weighted_anomaly = np.average(-scores, weights=weights)
        # Normalize to 0-30 range
        neural_score = float(np.clip(weighted_anomaly * 15, 0, 30))
    except Exception as e:
        log.warning("Anomaly detection failed: %s", e)
        return 0.0, []

    # Identify which vitals are trending most anomalously
    flagged: list[str] = []
    if neural_score > 10:
        for i, (code, name) in enumerate(zip(_FEATURE_CODES, _FEATURE_NAMES)):
            series = all_series.get(code)
            if series and series.current_rate is not None:
                # Flag vitals with high rate of change relative to their range
                if series.min_value is not None and series.max_value is not None:
                    val_range = series.max_value - series.min_value
                    if val_range > 0 and abs(series.current_rate) > val_range * 0.1:
                        flagged.append(
                            f"{name}: rate of change {series.current_rate:+.2f}/hr "
                            f"(range {val_range:.1f} over 72hr)"
                        )

    return round(neural_score, 2), flagged
