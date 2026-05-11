"""
Layer 5 — Neurosymbolic Acuity Scorer: SIRS criteria.

SIRS (Systemic Inflammatory Response Syndrome).
4 criteria, each scores +1. Positive = score ≥ 2.
"""

from __future__ import annotations
from vitalshield.temporal.models import TemporalProfile


def score_sirs(profile: TemporalProfile) -> tuple[int, list[str]]:
    """
    Returns (sirs_score, list_of_criteria_fired).
    sirs_score: 0-4
    """
    score = 0
    fired: list[str] = []
    vs = {**profile.vitals, **profile.labs}

    # 1. Temperature > 38°C or < 36°C
    temp_series = vs.get("8310-5")
    if temp_series and temp_series.current_value is not None:
        t = temp_series.current_value
        if t > 38.0:
            score += 1
            fired.append(f"Temp {t:.1f}°C > 38°C (fever)")
        elif t < 36.0:
            score += 1
            fired.append(f"Temp {t:.1f}°C < 36°C (hypothermia)")

    # 2. Heart rate > 90 bpm
    hr_series = vs.get("8867-4")
    if hr_series and hr_series.current_value is not None:
        if hr_series.current_value > 90:
            score += 1
            fired.append(f"HR {hr_series.current_value:.0f} > 90 bpm (tachycardia)")

    # 3. Respiratory rate > 20 OR PaCO2 < 32
    rr_series = vs.get("9279-1")
    pco2_series = vs.get("2019-8")
    rr_fired = False
    if rr_series and rr_series.current_value is not None:
        if rr_series.current_value > 20:
            score += 1
            fired.append(f"RR {rr_series.current_value:.0f} > 20 breaths/min (tachypnea)")
            rr_fired = True
    if not rr_fired and pco2_series and pco2_series.current_value is not None:
        if pco2_series.current_value < 32:
            score += 1
            fired.append(f"PaCO2 {pco2_series.current_value:.0f} < 32 mmHg")

    # 4. WBC > 12,000 or < 4,000 or > 10% bands
    wbc_series = vs.get("6690-2")
    if wbc_series and wbc_series.current_value is not None:
        wbc = wbc_series.current_value
        if wbc > 12000:
            score += 1
            fired.append(f"WBC {wbc:.0f}/μL > 12,000 (leukocytosis)")
        elif wbc < 4000:
            score += 1
            fired.append(f"WBC {wbc:.0f}/μL < 4,000 (leukopenia)")

    return score, fired
