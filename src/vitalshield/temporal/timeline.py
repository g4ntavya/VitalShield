"""
Layer 3 — Temporal Engine: 72-hour timeline builder.

Builds a complete velocity-aware temporal profile from a PatientProfile.
Each vital gets:
  - Sorted time series of readings
  - Rate of change (Δ/Δt) per point
  - Acceleration (Δ²/Δt²) per point
  - Trend classification
  - Gap-aware linear interpolation
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import numpy as np

from vitalshield.config import get_settings
from vitalshield.fhir.models import LabResult, PatientProfile, VitalReading
from vitalshield.temporal.models import TemporalProfile, TimeSeriesPoint, VitalTimeSeries

log = logging.getLogger(__name__)

# LOINC codes considered "vitals" vs "labs" — drives which sub-dict they go in
_VITAL_CODES = {
    "8867-4", "8480-6", "8462-4", "76536-0",
    "8310-5", "9279-1", "59408-5", "2708-6", "2710-2", "72514-3",
}


class TemporalEngine:
    """Builds 72-hour patient timeline with velocity vectors."""

    def __init__(self, window_hours: int | None = None, max_gap_minutes: int | None = None):
        settings = get_settings()
        self.window_hours = window_hours or settings.temporal_window_hours
        self.max_gap_minutes = max_gap_minutes or settings.temporal_max_gap_minutes

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_profile(self, patient: PatientProfile) -> TemporalProfile:
        """Build complete temporal profile from a PatientProfile."""
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=self.window_hours)

        profile = TemporalProfile(
            patient_id=patient.patient_id,
            window_hours=self.window_hours,
            window_start=window_start,
            window_end=now,
        )

        # Group readings by LOINC code for both vitals and labs
        vital_groups: dict[str, list[VitalReading]] = {}
        for reading in patient.vitals:
            ts = self._ensure_tz(reading.timestamp)
            if ts < window_start:
                continue
            vital_groups.setdefault(reading.code, []).append(reading)

        lab_groups: dict[str, list[LabResult]] = {}
        for lab in patient.labs:
            ts = self._ensure_tz(lab.timestamp)
            if ts < window_start:
                continue
            lab_groups.setdefault(lab.code, []).append(lab)

        total_points = 0
        interp_points = 0

        for code, readings in vital_groups.items():
            ts_obj = self._build_time_series(code, readings, is_vital=True)
            profile.vitals[code] = ts_obj
            total_points += len(ts_obj.points)
            interp_points += sum(1 for p in ts_obj.points if p.is_interpolated)

        for code, readings in lab_groups.items():
            ts_obj = self._build_time_series(code, readings, is_vital=False)
            profile.labs[code] = ts_obj
            total_points += len(ts_obj.points)
            interp_points += sum(1 for p in ts_obj.points if p.is_interpolated)

        profile.total_data_points = total_points
        profile.interpolated_points = interp_points
        profile.data_quality = (
            1.0 - (interp_points / total_points) if total_points > 0 else 0.0
        )

        return profile

    # ── Internal ───────────────────────────────────────────────────────────────

    def _build_time_series(
        self,
        code: str,
        readings: list[VitalReading] | list[LabResult],
        is_vital: bool,
    ) -> VitalTimeSeries:
        """Build a VitalTimeSeries for one LOINC code from raw readings."""
        # Sort chronologically
        sorted_readings = sorted(readings, key=lambda r: r.timestamp)

        display = sorted_readings[0].display
        unit = sorted_readings[0].unit

        # Build raw time series
        timestamps_raw = [self._ensure_tz(r.timestamp) for r in sorted_readings]
        values_raw = [r.value for r in sorted_readings]
        confidences_raw = [getattr(r, "confidence", 1.0) for r in sorted_readings]

        # Optionally interpolate gaps
        timestamps, values, confidences, is_interp = self._interpolate_gaps(
            timestamps_raw, values_raw, confidences_raw
        )

        # Compute derivatives
        rates = self._compute_rate_of_change(timestamps, values)
        accels = self._compute_acceleration(timestamps, rates)

        # Build point list
        points = []
        for i, (ts, val, conf, interp, rate, accel) in enumerate(
            zip(timestamps, values, confidences, is_interp, rates, accels)
        ):
            points.append(
                TimeSeriesPoint(
                    timestamp=ts,
                    value=round(val, 3),
                    rate_of_change=round(rate, 4) if rate is not None else None,
                    acceleration=round(accel, 4) if accel is not None else None,
                    is_interpolated=interp,
                    confidence=conf,
                )
            )

        # Summary stats
        vals_arr = np.array(values)
        current_value = values[-1] if values else None
        current_rate = rates[-1]
        current_accel = accels[-1]
        trend = self._classify_trend(rates, values)

        return VitalTimeSeries(
            loinc_code=code,
            display_name=display,
            unit=unit,
            points=points,
            current_value=round(current_value, 3) if current_value is not None else None,
            current_rate=round(current_rate, 4) if current_rate is not None else None,
            current_acceleration=round(current_accel, 4) if current_accel is not None else None,
            trend=trend,
            min_value=round(float(vals_arr.min()), 3) if len(vals_arr) > 0 else None,
            max_value=round(float(vals_arr.max()), 3) if len(vals_arr) > 0 else None,
            mean_value=round(float(vals_arr.mean()), 3) if len(vals_arr) > 0 else None,
        )

    def _interpolate_gaps(
        self,
        timestamps: list[datetime],
        values: list[float],
        confidences: list[float],
    ) -> tuple[list[datetime], list[float], list[float], list[bool]]:
        """Linear gap interpolation for gaps ≤ max_gap_minutes."""
        if len(timestamps) < 2:
            return timestamps, values, confidences, [False] * len(timestamps)

        out_ts, out_vals, out_cf, out_interp = [], [], [], []
        max_gap = timedelta(minutes=self.max_gap_minutes)

        for i in range(len(timestamps)):
            out_ts.append(timestamps[i])
            out_vals.append(values[i])
            out_cf.append(confidences[i])
            out_interp.append(False)

            if i < len(timestamps) - 1:
                gap = timestamps[i + 1] - timestamps[i]
                if timedelta(minutes=5) < gap <= max_gap:
                    # Insert one interpolated midpoint
                    mid_ts = timestamps[i] + gap / 2
                    mid_val = (values[i] + values[i + 1]) / 2
                    out_ts.append(mid_ts)
                    out_vals.append(mid_val)
                    out_cf.append(min(confidences[i], confidences[i + 1]) * 0.8)
                    out_interp.append(True)

        return out_ts, out_vals, out_cf, out_interp

    def _compute_rate_of_change(
        self,
        timestamps: list[datetime],
        values: list[float],
    ) -> list[float | None]:
        """Compute Δ/Δt per point (units per hour). None for first point."""
        rates: list[float | None] = [None]
        for i in range(1, len(timestamps)):
            dt_hours = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600
            if dt_hours == 0:
                rates.append(None)
            else:
                rates.append((values[i] - values[i - 1]) / dt_hours)
        return rates

    def _compute_acceleration(
        self,
        timestamps: list[datetime],
        rates: list[float | None],
    ) -> list[float | None]:
        """Compute Δ²/Δt² — acceleration of the rate of change."""
        accels: list[float | None] = [None, None]
        for i in range(2, len(timestamps)):
            if rates[i] is None or rates[i - 1] is None:
                accels.append(None)
            else:
                dt_hours = (timestamps[i] - timestamps[i - 1]).total_seconds() / 3600
                if dt_hours == 0:
                    accels.append(None)
                else:
                    accels.append((rates[i] - rates[i - 1]) / dt_hours)
        return accels

    def _classify_trend(
        self, rates: list[float | None], values: list[float]
    ) -> str:
        """Classify trend from recent rate-of-change values."""
        valid_rates = [r for r in rates[-5:] if r is not None]

        if len(valid_rates) < 2:
            return "insufficient_data"

        mean_rate = np.mean(valid_rates)
        std_rate = np.std(valid_rates)

        # High volatility
        if len(values) >= 2:
            value_range = max(values) - min(values)
            mean_val = np.mean(values)
            cv = value_range / mean_val if mean_val != 0 else 0
            if cv > 0.3 and std_rate > abs(mean_rate):
                return "volatile"

        # Direction
        if mean_rate > 0.5:
            return "rising"
        if mean_rate < -0.5:
            return "falling"
        return "stable"

    def _ensure_tz(self, dt: datetime) -> datetime:
        """Ensure datetime is timezone-aware (assume UTC if naive)."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
