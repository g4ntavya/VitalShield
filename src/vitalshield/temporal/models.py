"""
Layer 3 — Temporal Engine: Pydantic data models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TimeSeriesPoint(BaseModel):
    """A single point in a vital sign time series."""
    timestamp: datetime
    value: float
    rate_of_change: float | None = None       # Δ/Δt (units per hour)
    acceleration: float | None = None          # Δ²/Δt² (units per hour²)
    is_interpolated: bool = False
    confidence: float = 1.0                    # 0-1


class VitalTimeSeries(BaseModel):
    """Complete time series for a single vital sign."""
    loinc_code: str
    display_name: str
    unit: str
    points: list[TimeSeriesPoint] = Field(default_factory=list)
    current_value: float | None = None
    current_rate: float | None = None          # latest Δ/Δt
    current_acceleration: float | None = None  # latest Δ²/Δt²
    trend: Literal["rising", "falling", "stable", "volatile", "insufficient_data"] = "insufficient_data"
    min_value: float | None = None
    max_value: float | None = None
    mean_value: float | None = None


class TemporalProfile(BaseModel):
    """
    Complete 72-hour temporal profile for a patient.
    Keyed by LOINC code for O(1) vital lookup.
    """
    patient_id: str
    window_hours: int = 72
    window_start: datetime
    window_end: datetime
    vitals: dict[str, VitalTimeSeries] = Field(default_factory=dict)
    labs: dict[str, VitalTimeSeries] = Field(default_factory=dict)
    total_data_points: int = 0
    interpolated_points: int = 0
    data_quality: float = 1.0
