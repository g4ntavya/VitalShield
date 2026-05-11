"""
Layer 5 — Neurosymbolic Scorer: models.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SymbolicScoreDetail(BaseModel):
    qsofa_score: int
    qsofa_criteria: list[str]
    sirs_score: int
    sirs_criteria: list[str]
    news2_score: int
    news2_breakdown: list[str]
    news2_risk_level: str
    symbolic_total: float   # weighted, 0-70


class AcuityScore(BaseModel):
    """Full neurosymbolic acuity result."""
    patient_id: str
    acuity_score: float             # 0-100
    risk_level: str                 # low / moderate / high / critical
    symbolic: SymbolicScoreDetail
    neural_score: float             # 0-30
    neural_flagged_vitals: list[str]
    reasoning_trace: str            # human-readable explanation
    sepsis_positive: bool
    qsofa_positive: bool
    sirs_positive: bool
