"""
Layer 4 — CEP: Custom sliding-window Complex Event Processing engine.

Each pattern is a dataclass that defines:
  - A time window
  - A set of VitalCondition predicates
  - How many must be satisfied to fire

The engine evaluates all patterns against the TemporalProfile and returns
a list of CEPAlert objects for any that fire.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

from vitalshield.cep.models import CEPAlert, CEPResult
from vitalshield.temporal.models import TemporalProfile, VitalTimeSeries

log = logging.getLogger(__name__)


# ── Condition Primitives ────────────────────────────────────────────────────────

@dataclass
class VitalCondition:
    """
    A single predicate on a vital time series.
    op choices:
      gt / lt / gte / lte : current value comparison
      rise_pct             : current vs 6hr-ago percentage rise
      fall_pct             : current vs 6hr-ago percentage fall
      spike                : current value exceeds threshold (same as gt)
      rising               : rate_of_change > rate_threshold
      falling              : rate_of_change < rate_threshold
    """
    loinc_code: str
    op: str
    threshold: float = 0.0
    rate_threshold: float = 0.0

    def evaluate(self, series: VitalTimeSeries) -> tuple[bool, str]:
        """Return (satisfied, description)."""
        if not series.points:
            return False, f"{series.display_name}: no data"

        current = series.current_value
        rate = series.current_rate
        display = series.display_name

        if current is None:
            return False, f"{display}: no current value"

        if self.op == "gt" or self.op == "spike":
            ok = current > self.threshold
            return ok, f"{display} {current:.1f} > {self.threshold}"

        elif self.op == "lt":
            ok = current < self.threshold
            return ok, f"{display} {current:.1f} < {self.threshold}"

        elif self.op == "gte":
            ok = current >= self.threshold
            return ok, f"{display} {current:.1f} >= {self.threshold}"

        elif self.op == "lte":
            ok = current <= self.threshold
            return ok, f"{display} {current:.1f} <= {self.threshold}"

        elif self.op == "rise_pct":
            # Find value ~6 hours ago for comparison
            baseline = self._value_n_hours_ago(series, hours=6)
            if baseline is None or baseline == 0:
                return False, f"{display}: no baseline for rise_pct"
            pct = (current - baseline) / abs(baseline) * 100
            ok = pct > self.threshold
            return ok, f"{display} rose {pct:.1f}% (>{self.threshold}%)"

        elif self.op == "fall_pct":
            baseline = self._value_n_hours_ago(series, hours=6)
            if baseline is None or baseline == 0:
                return False, f"{display}: no baseline for fall_pct"
            pct = (baseline - current) / abs(baseline) * 100
            ok = pct > self.threshold
            return ok, f"{display} fell {pct:.1f}% (>{self.threshold}%)"

        elif self.op == "rising":
            if rate is None:
                return False, f"{display}: no rate of change"
            ok = rate > self.rate_threshold
            return ok, f"{display} rate {rate:.2f}/hr > {self.rate_threshold}"

        elif self.op == "falling":
            if rate is None:
                return False, f"{display}: no rate of change"
            ok = rate < self.rate_threshold
            return ok, f"{display} rate {rate:.2f}/hr < {self.rate_threshold}"

        return False, f"{display}: unknown op '{self.op}'"

    def _value_n_hours_ago(self, series: VitalTimeSeries, hours: float) -> float | None:
        """Return the best estimate of value N hours ago."""
        target = datetime.now(timezone.utc) - timedelta(hours=hours)
        best_point = None
        best_delta = timedelta(hours=999)
        for pt in series.points:
            ts = pt.timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta = abs(ts - target)
            if delta < best_delta:
                best_delta = delta
                best_point = pt
        return best_point.value if best_point else None


# ── Pattern Base ────────────────────────────────────────────────────────────────

@dataclass
class CEPPattern:
    name: str
    severity: Literal["warning", "critical", "emergency"]
    window_hours: float
    conditions: list[VitalCondition]
    min_conditions: int
    description: str
    recommendation: str = ""

    def evaluate(
        self, profile: TemporalProfile
    ) -> CEPAlert | None:
        """Evaluate this pattern against a temporal profile. Returns alert if fired."""
        all_series = {**profile.vitals, **profile.labs}
        matched = []
        evidence = {}

        for condition in self.conditions:
            series = all_series.get(condition.loinc_code)
            if series is None:
                continue

            satisfied, description = condition.evaluate(series)
            if satisfied:
                matched.append(description)
                if series.current_value is not None:
                    evidence[condition.loinc_code] = series.current_value

        if len(matched) >= self.min_conditions:
            return CEPAlert(
                pattern_name=self.name,
                severity=self.severity,
                fired_at=datetime.now(timezone.utc),
                window_hours=self.window_hours,
                matched_conditions=matched,
                total_conditions=len(self.conditions),
                min_conditions_required=self.min_conditions,
                evidence=evidence,
                description=self.description,
                recommendation=self.recommendation,
            )
        return None


# ── Pattern Library ─────────────────────────────────────────────────────────────

EARLY_SEPSIS = CEPPattern(
    name="early_sepsis",
    severity="critical",
    window_hours=6,
    conditions=[
        VitalCondition("8867-4",  op="rise_pct", threshold=20),    # HR rises >20%
        VitalCondition("76536-0", op="lt", threshold=65),           # MAP < 65 mmHg
        VitalCondition("8480-6",  op="lt", threshold=100),          # SBP < 100 mmHg (fallback if no MAP)
        VitalCondition("8310-5",  op="spike",  threshold=38.3),     # Temp spike >38.3°C
        VitalCondition("2524-7",  op="rising", rate_threshold=0),   # Lactate rising
        VitalCondition("9279-1",  op="gte",    threshold=22),       # RR >= 22
    ],
    min_conditions=3,
    description="Early Sepsis Signature: multi-vital deterioration pattern consistent with early sepsis.",
    recommendation="Consider: Blood cultures x2, broad-spectrum antibiotics within 1hr, IV fluid resuscitation 30mL/kg, lactate remeasurement, ICU consult.",
)

CARDIAC_DECOMPENSATION = CEPPattern(
    name="cardiac_decompensation",
    severity="critical",
    window_hours=4,
    conditions=[
        VitalCondition("8867-4",  op="gt",      threshold=120),     # HR > 120
        VitalCondition("8480-6",  op="falling", rate_threshold=-5), # SBP falling >5 mmHg/hr
        VitalCondition("59408-5", op="lt",      threshold=93),      # SpO2 < 93%
        VitalCondition("9279-1",  op="gt",      threshold=24),      # RR > 24
    ],
    min_conditions=3,
    description="Cardiac Decompensation: tachycardia + falling blood pressure + hypoxia pattern.",
    recommendation="Consider: ECG, troponin, BNP, echo, cardiology consult. Review fluid balance and vasopressor need.",
)

HEMORRHAGIC_SHOCK = CEPPattern(
    name="hemorrhagic_shock",
    severity="emergency",
    window_hours=2,
    conditions=[
        VitalCondition("8867-4",  op="rise_pct", threshold=30),     # HR rises >30%
        VitalCondition("8480-6",  op="lt",       threshold=90),     # SBP < 90
        VitalCondition("76536-0", op="falling",  rate_threshold=-3),# MAP falling
        VitalCondition("718-7",   op="falling",  rate_threshold=0), # Hemoglobin falling
    ],
    min_conditions=2,
    description="Hemorrhagic Shock: compensatory tachycardia + hypotension + falling hemoglobin pattern.",
    recommendation="EMERGENCY: Activate massive transfusion protocol. Type & crossmatch. Surgical consult STAT. 2x large-bore IV access.",
)

RESPIRATORY_FAILURE = CEPPattern(
    name="respiratory_failure",
    severity="critical",
    window_hours=3,
    conditions=[
        VitalCondition("59408-5", op="lt",  threshold=90),          # SpO2 < 90%
        VitalCondition("9279-1",  op="gt",  threshold=28),          # RR > 28
        VitalCondition("2019-8",  op="gt",  threshold=45),          # PaCO2 > 45
        VitalCondition("2703-7",  op="lt",  threshold=60),          # PaO2 < 60
    ],
    min_conditions=2,
    description="Acute Respiratory Failure: hypoxia + tachypnea pattern with impending respiratory collapse.",
    recommendation="Consider: ABG, CXR, escalate O2. If SpO2 <88%: non-rebreather mask or NIV. Anesthesia/ICU if deteriorating.",
)

SEPTIC_SHOCK = CEPPattern(
    name="septic_shock",
    severity="emergency",
    window_hours=3,
    conditions=[
        VitalCondition("8480-6",  op="lt",      threshold=90),      # SBP < 90 despite resuscitation
        VitalCondition("2524-7",  op="gt",      threshold=2.0),     # Lactate > 2 mmol/L
        VitalCondition("8867-4",  op="gt",      threshold=110),     # HR > 110
        VitalCondition("8310-5",  op="gt",      threshold=38.0),    # Temp > 38
    ],
    min_conditions=3,
    description="Septic Shock signature",
    recommendation="EMERGENCY: Vasopressors (norepinephrine first-line). MAP target >65. Repeat lactate. Blood cultures. Antibiotics STAT. ICU transfer.",
)

RENAL_DECLINE = CEPPattern(
    name="renal_decline",
    severity="critical",
    window_hours=6,
    conditions=[
        VitalCondition("2160-0",  op="rising",  rate_threshold=0.03), # Creatinine rising
        VitalCondition("2160-0",  op="gt",      threshold=1.2),       # Creatinine > 1.2
        VitalCondition("3094-0",  op="rising",  rate_threshold=0.5),  # BUN rising
    ],
    min_conditions=2,
    description="Renal function declining (creatinine/BUN trend)",
    recommendation="Consider: Nephrology consult, review nephrotoxic medications, assess fluid status.",
)

FLUID_OVERLOAD_RISK = CEPPattern(
    name="fluid_balance_trending",
    severity="warning",
    window_hours=12,
    conditions=[
        VitalCondition("31688-5", op="lt",      threshold=-500),    # Negative fluid balance
        VitalCondition("31688-5", op="gt",      threshold=2000),    # OR strong positive balance (will trigger separately)
        VitalCondition("59408-5", op="falling", rate_threshold=-1), # SpO2 dropping
        VitalCondition("9279-1",  op="rising",  rate_threshold=1),  # RR rising
    ],
    min_conditions=2,
    description="Fluid balance trending negatively or volume overload risk.",
    recommendation="Consider: Reassess fluid administration vs diuresis. Monitor pulmonary status.",
)

POST_OP_COMPLICATION = CEPPattern(
    name="post_op_complication",
    severity="critical",
    window_hours=8,
    conditions=[
        VitalCondition("8867-4",  op="gt",      threshold=105),     # Tachycardia
        VitalCondition("8310-5",  op="spike",   threshold=38.3),    # Fever spike
        VitalCondition("6690-2",  op="rise_pct",threshold=15),      # WBC rising
    ],
    min_conditions=2,
    description="Post-op complication signature (SIRS/infection).",
    recommendation="Consider: Source control check, surgical site inspection, review prophylactic antibiotics.",
)

HEMODYNAMIC_INSTABILITY = CEPPattern(
    name="medication_response_failure",
    severity="emergency",
    window_hours=2,
    conditions=[
        VitalCondition("76536-0", op="falling", rate_threshold=-2), # MAP falling
        VitalCondition("8480-6",  op="falling", rate_threshold=-5), # SBP falling
        VitalCondition("8867-4",  op="rising",  rate_threshold=5),  # HR rising to compensate
    ],
    min_conditions=2,
    description="Hemodynamic instability / Failing medication response.",
    recommendation="Consider: Evaluate fluid responsiveness vs escalating vasopressors. Recheck perfusion markers.",
)


ALL_PATTERNS: list[CEPPattern] = [
    EARLY_SEPSIS,
    CARDIAC_DECOMPENSATION,
    HEMORRHAGIC_SHOCK,
    RESPIRATORY_FAILURE,
    SEPTIC_SHOCK,
    RENAL_DECLINE,
    FLUID_OVERLOAD_RISK,
    POST_OP_COMPLICATION,
    HEMODYNAMIC_INSTABILITY,
]


# ── CEP Engine ──────────────────────────────────────────────────────────────────

class CEPEngine:
    """
    Custom sliding-window Complex Event Processing engine.
    Evaluates all registered patterns against a TemporalProfile.
    """

    def __init__(self, patterns: list[CEPPattern] | None = None):
        self.patterns = patterns or ALL_PATTERNS

    def evaluate(self, profile: TemporalProfile) -> CEPResult:
        """Run all patterns against the temporal profile."""
        alerts: list[CEPAlert] = []

        for pattern in self.patterns:
            try:
                alert = pattern.evaluate(profile)
                if alert:
                    alerts.append(alert)
                    log.info(
                        "CEP ALERT patient=%s pattern=%s severity=%s conditions=%d/%d",
                        profile.patient_id,
                        pattern.name,
                        alert.severity,
                        len(alert.matched_conditions),
                        alert.total_conditions,
                    )
            except Exception as e:
                log.error("CEP pattern %s evaluation error: %s", pattern.name, e)

        # Sort by severity
        severity_order = {"emergency": 0, "critical": 1, "warning": 2}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 99))

        highest = "none"
        if alerts:
            highest = alerts[0].severity  # type: ignore[assignment]

        # Sepsis-specific risk score
        sepsis_matched = any(a.pattern_name in ("early_sepsis", "septic_shock") for a in alerts)
        sepsis_score = 0.0
        for alert in alerts:
            if alert.pattern_name == "septic_shock":
                sepsis_score = max(sepsis_score, 0.9)
            elif alert.pattern_name == "early_sepsis":
                ratio = len(alert.matched_conditions) / alert.total_conditions
                sepsis_score = max(sepsis_score, 0.5 + ratio * 0.4)

        return CEPResult(
            patient_id=profile.patient_id,
            alerts=alerts,
            patterns_evaluated=len(self.patterns),
            highest_severity=highest,  # type: ignore[arg-type]
            sepsis_risk_score=round(sepsis_score, 3),
            sepsis_pattern_matched=sepsis_matched,
        )
