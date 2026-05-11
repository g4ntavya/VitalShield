"""
Layer 4 — CEP: Data models for pattern matches and alerts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class CEPAlert(BaseModel):
    """A fired CEP pattern alert."""
    pattern_name: str
    severity: Literal["warning", "critical", "emergency"]
    fired_at: datetime
    window_hours: float
    matched_conditions: list[str]              # which vital conditions fired
    total_conditions: int
    min_conditions_required: int
    evidence: dict[str, float]                 # vital code → value at fire time
    description: str
    recommendation: str = ""


class CEPResult(BaseModel):
    """Result of running the CEP engine on a patient."""
    patient_id: str
    evaluated_at: datetime = Field(default_factory=datetime.utcnow)
    alerts: list[CEPAlert] = Field(default_factory=list)
    patterns_evaluated: int = 0
    highest_severity: Literal["none", "warning", "critical", "emergency"] = "none"
    sepsis_risk_score: float = 0.0     # 0-1 probability estimate
    sepsis_pattern_matched: bool = False
