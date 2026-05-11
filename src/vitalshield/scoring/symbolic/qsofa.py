"""
Layer 5 — Neurosymbolic Acuity Scorer: qSOFA.

qSOFA (quick Sequential Organ Failure Assessment) from Sepsis-3.
3 criteria, each scores +1. Positive = score ≥ 2.
"""

from __future__ import annotations
from vitalshield.temporal.models import TemporalProfile


def score_qsofa(profile: TemporalProfile) -> tuple[int, list[str]]:
    """
    Returns (qsofa_score, list_of_criteria_fired).
    qsofa_score: 0-3
    """
    score = 0
    fired: list[str] = []
    vs = {**profile.vitals, **profile.labs}

    # 1. Respiratory rate >= 22
    rr_series = vs.get("9279-1")
    if rr_series and rr_series.current_value is not None:
        if rr_series.current_value >= 22:
            score += 1
            fired.append(f"RR {rr_series.current_value:.0f} ≥ 22 breaths/min")

    # 2. Altered mentation (GCS < 15) — proxy: we check if GCS series exists
    gcs_series = vs.get("72514-3")
    if gcs_series and gcs_series.current_value is not None:
        if gcs_series.current_value < 15:
            score += 1
            fired.append(f"GCS {gcs_series.current_value:.0f} < 15 (altered mentation)")

    # 3. Systolic BP <= 100
    sbp_series = vs.get("8480-6")
    if sbp_series and sbp_series.current_value is not None:
        if sbp_series.current_value <= 100:
            score += 1
            fired.append(f"SBP {sbp_series.current_value:.0f} ≤ 100 mmHg")

    return score, fired
