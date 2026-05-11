"""
Layer 5 — Neurosymbolic Acuity Scorer: NEWS2.

National Early Warning Score 2. 7 parameters, total score 0-20+.
Score ≥ 5 = urgent clinical review. Score ≥ 7 = emergency response.
"""

from __future__ import annotations
from vitalshield.temporal.models import TemporalProfile


def score_news2(profile: TemporalProfile) -> tuple[int, list[str]]:
    """
    Returns (news2_total, list_of_parameter_scores).
    news2_total: 0+ (typically 0-20)
    """
    total = 0
    breakdown: list[str] = []
    vs = {**profile.vitals, **profile.labs}

    # 1. Respiratory Rate
    rr_series = vs.get("9279-1")
    if rr_series and rr_series.current_value is not None:
        rr = rr_series.current_value
        if rr <= 8:
            pts = 3
        elif rr <= 11:
            pts = 1
        elif rr <= 20:
            pts = 0
        elif rr <= 24:
            pts = 2
        else:
            pts = 3
        total += pts
        breakdown.append(f"RR {rr:.0f}/min → NEWS2 +{pts}")

    # 2. SpO2 (Scale 1 — no hypercapnic failure)
    spo2_series = vs.get("59408-5") or vs.get("2710-2") or vs.get("2708-6")
    if spo2_series and spo2_series.current_value is not None:
        spo2 = spo2_series.current_value
        if spo2 <= 91:
            pts = 3
        elif spo2 <= 93:
            pts = 2
        elif spo2 <= 95:
            pts = 1
        else:
            pts = 0
        total += pts
        breakdown.append(f"SpO2 {spo2:.0f}% → NEWS2 +{pts}")

    # 3. Systolic Blood Pressure
    sbp_series = vs.get("8480-6")
    if sbp_series and sbp_series.current_value is not None:
        sbp = sbp_series.current_value
        if sbp <= 90:
            pts = 3
        elif sbp <= 100:
            pts = 2
        elif sbp <= 110:
            pts = 1
        elif sbp <= 219:
            pts = 0
        else:
            pts = 3
        total += pts
        breakdown.append(f"SBP {sbp:.0f} mmHg → NEWS2 +{pts}")

    # 4. Pulse (Heart Rate)
    hr_series = vs.get("8867-4")
    if hr_series and hr_series.current_value is not None:
        hr = hr_series.current_value
        if hr <= 40:
            pts = 3
        elif hr <= 50:
            pts = 1
        elif hr <= 90:
            pts = 0
        elif hr <= 110:
            pts = 1
        elif hr <= 130:
            pts = 2
        else:
            pts = 3
        total += pts
        breakdown.append(f"HR {hr:.0f} bpm → NEWS2 +{pts}")

    # 5. Level of Consciousness (GCS proxy — 0 if alert, 3 if CVPU)
    gcs_series = vs.get("72514-3")
    if gcs_series and gcs_series.current_value is not None:
        gcs = gcs_series.current_value
        if gcs < 15:
            total += 3
            breakdown.append(f"GCS {gcs:.0f} (altered consciousness) → NEWS2 +3")
        else:
            breakdown.append("GCS 15 (alert) → NEWS2 +0")

    # 6. Temperature
    temp_series = vs.get("8310-5")
    if temp_series and temp_series.current_value is not None:
        t = temp_series.current_value
        if t <= 35.0:
            pts = 3
        elif t <= 36.0:
            pts = 1
        elif t <= 38.0:
            pts = 0
        elif t <= 39.0:
            pts = 1
        else:
            pts = 2
        total += pts
        breakdown.append(f"Temp {t:.1f}°C → NEWS2 +{pts}")

    return total, breakdown


def news2_risk_level(score: int) -> str:
    """Classify NEWS2 score into clinical risk level."""
    if score <= 4:
        return "low"
    if score <= 6:
        return "medium"
    return "high"
