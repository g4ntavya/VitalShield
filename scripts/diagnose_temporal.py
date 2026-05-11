"""
Temporal Accuracy Diagnostic Script.

Tests:
  1. Rate of change accuracy (vs. finite differences ground truth)
  2. Interpolation accuracy (known-value reconstruction)
  3. Trend classification confusion matrix
  4. Acceleration vector accuracy
  5. Window boundary behavior
"""

from __future__ import annotations
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncio
from datetime import datetime, timedelta, timezone
import numpy as np

from vitalshield.fhir.models import PatientProfile, Demographics, VitalReading
from vitalshield.temporal.timeline import TemporalEngine


# ── Helper: build a synthetic patient with known exact values ─────────────────

def make_exact_patient(
    patient_id: str,
    code: str,
    display: str,
    unit: str,
    timestamps_hours_ago: list[float],
    values: list[float],
) -> PatientProfile:
    now = datetime.now(timezone.utc)
    vitals = [
        VitalReading(
            code=code, display=display, value=v, unit=unit,
            timestamp=now - timedelta(hours=h)
        )
        for h, v in zip(timestamps_hours_ago, values)
    ]
    return PatientProfile(
        patient_id=patient_id,
        demographics=Demographics(patient_id=patient_id, name="Test"),
        vitals=vitals,
    )


def print_section(title: str):
    print(f"\n{'═'*60}")
    print(f"  {title}")
    print(f"{'═'*60}")


def print_ok(msg):   print(f"  ✅ {msg}")
def print_warn(msg): print(f"  ⚠️  {msg}")
def print_fail(msg): print(f"  ❌ {msg}")


engine = TemporalEngine(window_hours=72, max_gap_minutes=30)


# ════════════════════════════════════════════════════════════
# TEST 1: Rate of Change Accuracy
# ════════════════════════════════════════════════════════════
print_section("TEST 1: Rate of Change (Δ/Δt) Accuracy")

# Linear rise: HR goes from 80 → 140 over 12 hours
# Ground truth rate = (140-80)/12 = +5.0 bpm/hr
hr_values  = [80.0, 90.0, 100.0, 110.0, 120.0, 130.0, 140.0]
hr_timestamps = [12.0, 10.0, 8.0, 6.0, 4.0, 2.0, 0.0]  # hours ago

patient_linear = make_exact_patient(
    "test_linear", "8867-4", "Heart rate", "/min",
    hr_timestamps, hr_values
)
profile_linear = engine.build_profile(patient_linear)
hr_series = profile_linear.vitals["8867-4"]

expected_rate = (140.0 - 130.0) / 2.0   # last segment: 10 bpm over 2 hrs = 5.0 bpm/hr
actual_rate = hr_series.current_rate
error = abs(actual_rate - expected_rate) if actual_rate else float('inf')
error_pct = error / expected_rate * 100

if error_pct < 0.01:
    print_ok(f"Linear rate: expected {expected_rate:.2f}, got {actual_rate:.6f}, error {error:.2e} bpm/hr ({error_pct:.4f}%)")
elif error_pct < 1.0:
    print_warn(f"Linear rate: expected {expected_rate:.2f}, got {actual_rate:.6f}, error {error:.4f} bpm/hr ({error_pct:.2f}%)")
else:
    print_fail(f"Linear rate: expected {expected_rate:.2f}, got {actual_rate:.6f}, error {error:.4f} bpm/hr ({error_pct:.2f}%)")

# Check all intermediate rates
all_rates = [p.rate_of_change for p in hr_series.points if p.rate_of_change is not None]
all_errors = [abs(r - 5.0) for r in all_rates]
max_err = max(all_errors) if all_errors else float('inf')
mean_err = np.mean(all_errors) if all_errors else float('inf')
print(f"  All segment rates: {[f'{r:.4f}' for r in all_rates]}")
print(f"  Max error across all segments: {max_err:.6f} bpm/hr | Mean error: {mean_err:.6f}")

if max_err < 0.0001:
    print_ok(f"All segment rates accurate to 4 decimal places")
else:
    print_warn(f"Max rate error: {max_err:.6f} (may be floating point)")

# Non-linear (sinusoidal) — test we compute finite differences correctly, not assume linearity
print("\n  [Non-linear test] Sinusoidal HR pattern:")
angles = np.linspace(0, 2*np.pi, 12)
sin_values = [100 + 20 * math.sin(a) for a in angles]
sin_hours = [11.0 - i for i in range(12)]

patient_sin = make_exact_patient(
    "test_sin", "8867-4", "Heart rate", "/min",
    sin_hours, sin_values
)
profile_sin = engine.build_profile(patient_sin)
sin_series = profile_sin.vitals["8867-4"]
sin_rates = [p.rate_of_change for p in sin_series.points if p.rate_of_change is not None]
print(f"  N rates computed: {len(sin_rates)} / {len(sin_values)-1} expected")
print(f"  Rate range: [{min(sin_rates):.2f}, {max(sin_rates):.2f}] bpm/hr (should vary, not be constant)")
# For sinusoidal, rates should oscillate
rate_variance = np.std(sin_rates)
if rate_variance > 5.0:
    print_ok(f"Sinusoidal rates show correct variance (std={rate_variance:.2f})")
else:
    print_fail(f"Rates not oscillating? std={rate_variance:.2f}")


# ════════════════════════════════════════════════════════════
# TEST 2: Acceleration Accuracy
# ════════════════════════════════════════════════════════════
print_section("TEST 2: Acceleration (Δ²/Δt²) Accuracy")

# Quadratic: value = 100 + 5*t² → rate = 10t → accel = 10
# With hourly readings: t=0,1,2,3,4,5,6 (h ago reversed)
# Values: at t=6h ago: 100+5*0=100, at t=5h ago: 100+5*1=105, ...
# So hours_ago = [6,5,4,3,2,1,0], values = [100,105,120,145,180,225,280]
quad_hours   = [6.0, 5.0, 4.0, 3.0, 2.0, 1.0, 0.0]
quad_values  = [100.0 + 5*i**2 for i in range(7)]  # i=time units from start

patient_quad = make_exact_patient(
    "test_quad", "8867-4", "Heart rate", "/min",
    quad_hours, quad_values
)
profile_quad = engine.build_profile(patient_quad)
quad_series = profile_quad.vitals["8867-4"]
accels = [p.acceleration for p in quad_series.points if p.acceleration is not None]
print(f"  Quadratic f(t)=100+5t²: rate=10t, accel=10 (constant)")
print(f"  Computed accelerations: {[f'{a:.2f}' for a in accels]}")
# True acceleration should be 10 bpm/hr² in all cases  (second diff of 5t²)
# Between t=6,5,4: Δv at t=5: 5 bpm/hr, Δv at t=4: 15 bpm/hr → accel = 10
accel_errors = [abs(a - 10.0) for a in accels]
if accel_errors:
    max_accel_err = max(accel_errors)
    if max_accel_err < 0.001:
        print_ok(f"Acceleration accurate: max error {max_accel_err:.6f} bpm/hr²")
    elif max_accel_err < 1.0:
        print_warn(f"Acceleration approx: max error {max_accel_err:.4f} bpm/hr²")
    else:
        print_fail(f"Acceleration error too high: {max_accel_err:.4f} bpm/hr²")
else:
    print_warn("No accelerations computed (need ≥3 points after interpolation)")


# ════════════════════════════════════════════════════════════
# TEST 3: Interpolation Accuracy
# ════════════════════════════════════════════════════════════
print_section("TEST 3: Gap Interpolation Accuracy")

# Create a reading with a gap: 70h ago and 67h ago (3hr gap < 30min? No, 3 hours)
# Gaps <= max_gap_minutes=30min get interpolated. 3hr gap = NOT interpolated.
# Test a 15min (=0.25hr) gap that SHOULD be interpolated:
now = datetime.now(timezone.utc)
gap_readings = [
    VitalReading(code="8867-4", display="Heart rate", value=80.0, unit="/min",
                 timestamp=now - timedelta(hours=2)),
    # 25min gap — SHOULD be interpolated (< 30min max_gap)
    VitalReading(code="8867-4", display="Heart rate", value=90.0, unit="/min",
                 timestamp=now - timedelta(minutes=95)),  # 95 min ago
    # Then immediate next reading
    VitalReading(code="8867-4", display="Heart rate", value=100.0, unit="/min",
                 timestamp=now - timedelta(minutes=5)),
]

# Note: gap between reading 1 (120min) and reading 2 (95min) = 25min → interpolated
# gap between reading 2 (95min) and reading 3 (5min) = 90min → NOT interpolated (>30min)

patient_gap = PatientProfile(
    patient_id="test_gap",
    demographics=Demographics(patient_id="test_gap", name="Test"),
    vitals=gap_readings,
)
profile_gap = engine.build_profile(patient_gap)
gap_series = profile_gap.vitals["8867-4"]
n_total = len(gap_series.points)
n_interp = sum(1 for p in gap_series.points if p.is_interpolated)

print(f"  Original readings: 3 | After interpolation: {n_total} points ({n_interp} interpolated)")

if n_total == 4 and n_interp == 1:
    interp_pt = next(p for p in gap_series.points if p.is_interpolated)
    expected_interp_val = (80.0 + 90.0) / 2.0  # midpoint
    interp_error = abs(interp_pt.value - expected_interp_val)
    if interp_error < 0.001:
        print_ok(f"Interpolated value: {interp_pt.value:.4f} (expected {expected_interp_val:.1f}, error {interp_error:.6f})")
    else:
        print_warn(f"Interpolation error: {interp_error:.4f}")
    print(f"  Interpolated confidence: {interp_pt.confidence:.2f} (expected ~0.8)")
    if abs(interp_pt.confidence - 0.8) < 0.1:
        print_ok(f"Confidence correctly penalized for interpolated point")
    else:
        print_warn(f"Confidence: {interp_pt.confidence:.2f}")
elif n_interp == 0:
    print_warn(f"No interpolation occurred — gap may have been outside threshold window")
    print(f"  Points: {[(f'{p.value:.0f}', p.is_interpolated) for p in gap_series.points]}")
else:
    print_warn(f"Unexpected: {n_total} points, {n_interp} interpolated")


# ════════════════════════════════════════════════════════════
# TEST 4: Trend Classification Accuracy
# ════════════════════════════════════════════════════════════
print_section("TEST 4: Trend Classification Accuracy")

test_cases = [
    # (description, hours_ago, values, expected_trend)
    ("Strong rise (+10/hr)",    [5,4,3,2,1,0], [80,90,100,110,120,130], "rising"),
    ("Strong fall (-8/hr)",     [5,4,3,2,1,0], [130,122,114,106,98,90], "falling"),
    ("Stable (flat ±1)",        [5,4,3,2,1,0], [100,101,100,99,100,101], "stable"),
    ("Volatile (oscillating)", [5,4,3,2,1,0], [80,120,75,125,70,130], "volatile"),
]

correct = 0
for desc, hours, values, expected in test_cases:
    p = make_exact_patient("trend_test", "8867-4", "Heart rate", "/min", hours, values)
    prof = engine.build_profile(p)
    s = prof.vitals["8867-4"]
    actual = s.trend
    ok = actual == expected
    if ok:
        correct += 1
        print_ok(f"{desc}: got '{actual}' ✓")
    else:
        print_warn(f"{desc}: expected '{expected}', got '{actual}'")

print(f"\n  Trend accuracy: {correct}/{len(test_cases)} ({correct/len(test_cases)*100:.0f}%)")


# ════════════════════════════════════════════════════════════
# TEST 5: Window Boundary — readings outside 72hr window
# ════════════════════════════════════════════════════════════
print_section("TEST 5: 72-Hour Window Boundary")

boundary_patient = make_exact_patient(
    "window_test", "8867-4", "Heart rate", "/min",
    [80.0, 73.0, 72.5, 71.9, 24.0, 1.0],  # hours ago
    [70.0, 80.0, 90.0, 92.0, 100.0, 110.0],
)
profile_window = engine.build_profile(boundary_patient)
ws = profile_window.vitals.get("8867-4")
if ws:
    timestamps_included = len(ws.points)
    # 80h, 73h, 72.5h are ALL > 72h ago → outside window (strict < check)
    # 71.9h, 24h, 1h are inside → 3 expected
    print(f"  Readings provided: 6 (80h, 73h, 72.5h outside window | 71.9h, 24h, 1h inside)")
    print(f"  Readings in profile: {timestamps_included} (expected: 3)")
    if timestamps_included == 3:
        print_ok("72hr window filtering: CORRECT (strict boundary, 3 readings included)")
    elif timestamps_included == 4:
        print_warn("Inclusive boundary — 72.5h reading unexpectedly included")
    else:
        print_fail(f"Window filtering wrong: {timestamps_included} points")
else:
    print_warn("No HR series (all outside window)")


# ════════════════════════════════════════════════════════════
# TEST 6: Real Sepsis Patient  
# ════════════════════════════════════════════════════════════
print_section("TEST 6: Synthetic Sepsis Patient — Full Stack Accuracy")

async def test_sepsis():
    from vitalshield.fhir.client import FHIRClient
    from vitalshield.cep.engine import CEPEngine
    from vitalshield.scoring.scorer import score_patient

    client = FHIRClient()
    patient = await client.get_patient_profile("sepsis-demo")
    profile = engine.build_profile(patient)
    cep = CEPEngine()
    cep_result = cep.evaluate(profile)
    score = score_patient(profile)

    hr = profile.vitals.get("8867-4")
    sbp = profile.vitals.get("8480-6")
    lactate = profile.labs.get("2524-7")

    print(f"  HR current: {hr.current_value} bpm, trend: {hr.trend}, rate: {hr.current_rate:+.2f}/hr")
    print(f"  SBP current: {sbp.current_value} mmHg, trend: {sbp.trend}, rate: {sbp.current_rate:+.2f}/hr")
    print(f"  Lactate current: {lactate.current_value} mmol/L, trend: {lactate.trend}")

    # Ground truth from synthetic data:
    # HR: 88 → 92 → 98 → 105 → 115 → 124 over 72hr
    # HR last segment: (124-115)/(12-1) = 9/11 ≈ 0.818 bpm/hr  
    expected_hr_rate = (124.0 - 115.0) / (12.0 - 1.0)
    if hr.current_rate:
        hr_rate_error = abs(hr.current_rate - expected_hr_rate)
        if hr_rate_error < 0.005:
            print_ok(f"HR rate: expected {expected_hr_rate:.4f}, got {hr.current_rate:.4f}, error {hr_rate_error:.6f}")
        else:
            print_warn(f"HR rate: expected {expected_hr_rate:.4f}, got {hr.current_rate:.4f}")

    # SBP: 128 → 120 → 108 → 95 → 88 over 72→6→1hr
    # Last segment: (88-95)/(6-1)h = -7/5 = -1.4 mmHg/hr
    expected_sbp_rate = (88.0 - 95.0) / (6.0 - 1.0)
    if sbp.current_rate:
        sbp_rate_error = abs(sbp.current_rate - expected_sbp_rate)
        if sbp_rate_error < 0.005:
            print_ok(f"SBP rate: expected {expected_sbp_rate:.4f}, got {sbp.current_rate:.4f}, error {sbp_rate_error:.6f}")
        else:
            print_warn(f"SBP rate: expected {expected_sbp_rate:.4f}, got {sbp.current_rate:.4f}")

    print(f"\n  CEP alerts: {len(cep_result.alerts)} patterns fired")
    for a in cep_result.alerts:
        print(f"    [{a.severity}] {a.pattern_name}: {len(a.matched_conditions)}/{a.total_conditions} conditions")

    print(f"\n  Acuity: {score.acuity_score}/100 | Risk: {score.risk_level}")
    print(f"  qSOFA: {score.symbolic.qsofa_score}/3 | SIRS: {score.symbolic.sirs_score}/4 | NEWS2: {score.symbolic.news2_score}")

asyncio.run(test_sepsis())

print_section("SUMMARY")
print()
