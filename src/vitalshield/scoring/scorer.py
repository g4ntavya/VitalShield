"""
Layer 5 — Neurosymbolic Scorer: Hybrid orchestrator.

Combines symbolic clinical rules (qSOFA + SIRS + NEWS2) with
neural anomaly detection (Isolation Forest) into a single 0-100 score.
"""

from __future__ import annotations

import logging

from vitalshield.scoring.models import AcuityScore, SymbolicScoreDetail
from vitalshield.scoring.neural.anomaly import detect_anomaly
from vitalshield.scoring.symbolic.news2 import news2_risk_level, score_news2
from vitalshield.scoring.symbolic.qsofa import score_qsofa
from vitalshield.scoring.symbolic.sirs import score_sirs
from vitalshield.temporal.models import TemporalProfile

log = logging.getLogger(__name__)


def score_patient(profile: TemporalProfile) -> AcuityScore:
    """
    Run full neurosymbolic scoring pipeline on a TemporalProfile.
    
    Symbolic component (max 70):
        qSOFA  × 15  → max 45 contribution (3 criteria)
        SIRS   × 8   → max 32 contribution (4 criteria)  (shared cap)
        NEWS2  × 2.3 → max 23 contribution (score ≥ 10 capped)
        Total capped at 70 to leave room for neural component.
    
    Neural component (max 30):
        Isolation Forest anomaly score on 72hr vital trajectory.

    Combined: 0-100.
    """
    # ── Symbolic ────────────────────────────────────────────────────────────────
    qsofa_score, qsofa_fired = score_qsofa(profile)
    sirs_score, sirs_fired = score_sirs(profile)
    news2_score, news2_breakdown = score_news2(profile)

    # Weighted combination
    qsofa_contrib = qsofa_score * 15         # 0-45
    sirs_contrib = sirs_score * 8            # 0-32
    news2_contrib = min(news2_score, 10) * 2.3  # 0-23

    symbolic_raw = qsofa_contrib + sirs_contrib + news2_contrib
    symbolic_total = min(symbolic_raw, 70.0)

    # ── Neural ──────────────────────────────────────────────────────────────────
    neural_score, neural_flagged = detect_anomaly(profile)

    # ── Combined ────────────────────────────────────────────────────────────────
    acuity_score = round(symbolic_total + neural_score, 1)
    acuity_score = min(acuity_score, 100.0)

    # ── Risk Level ─────────────────────────────────────────────────────────────
    if acuity_score >= 75:
        risk_level = "critical"
    elif acuity_score >= 50:
        risk_level = "high"
    elif acuity_score >= 25:
        risk_level = "moderate"
    else:
        risk_level = "low"

    # ── Reasoning Trace ────────────────────────────────────────────────────────
    trace_parts = [
        f"ACUITY SCORE: {acuity_score:.1f}/100 — {risk_level.upper()}",
        "",
        f"SYMBOLIC COMPONENT: {symbolic_total:.1f}/70",
        f"  qSOFA {qsofa_score}/3 (positive={'YES' if qsofa_score >= 2 else 'NO'}): {'; '.join(qsofa_fired) or 'No criteria met'}",
        f"  SIRS {sirs_score}/4 (positive={'YES' if sirs_score >= 2 else 'NO'}): {'; '.join(sirs_fired) or 'No criteria met'}",
        f"  NEWS2 {news2_score} ({news2_risk_level(news2_score)} risk):",
    ]
    for line in news2_breakdown:
        trace_parts.append(f"    {line}")

    trace_parts += [
        "",
        f"NEURAL COMPONENT: {neural_score:.1f}/30",
    ]
    if neural_flagged:
        for flag in neural_flagged:
            trace_parts.append(f"  ⚠  {flag}")
    else:
        trace_parts.append("  No anomalous vital trajectories detected.")

    if qsofa_score >= 2 or sirs_score >= 2:
        trace_parts += [
            "",
            "SEPSIS SCREENING POSITIVE — recommend immediate clinical evaluation.",
        ]

    reasoning_trace = "\n".join(trace_parts)

    return AcuityScore(
        patient_id=profile.patient_id,
        acuity_score=acuity_score,
        risk_level=risk_level,
        symbolic=SymbolicScoreDetail(
            qsofa_score=qsofa_score,
            qsofa_criteria=qsofa_fired,
            sirs_score=sirs_score,
            sirs_criteria=sirs_fired,
            news2_score=news2_score,
            news2_breakdown=news2_breakdown,
            news2_risk_level=news2_risk_level(news2_score),
            symbolic_total=round(symbolic_total, 2),
        ),
        neural_score=neural_score,
        neural_flagged_vitals=neural_flagged,
        reasoning_trace=reasoning_trace,
        sepsis_positive=(qsofa_score >= 2 or sirs_score >= 2),
        qsofa_positive=(qsofa_score >= 2),
        sirs_positive=(sirs_score >= 2),
    )
