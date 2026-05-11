"""Tests for Layer 3 — Temporal engine."""

import pytest
from vitalshield.fhir.client import FHIRClient
from vitalshield.temporal.timeline import TemporalEngine


@pytest.mark.asyncio
async def test_temporal_profile_has_vitals():
    client = FHIRClient()
    engine = TemporalEngine()
    patient = await client.get_patient_profile("test-001")
    profile = engine.build_profile(patient)

    assert profile.patient_id == "test-001"
    assert len(profile.vitals) > 0
    assert profile.total_data_points > 0


@pytest.mark.asyncio
async def test_hr_trend_is_rising():
    client = FHIRClient()
    engine = TemporalEngine()
    patient = await client.get_patient_profile("sepsis-test")
    profile = engine.build_profile(patient)

    hr_series = profile.vitals.get("8867-4")
    assert hr_series is not None
    assert hr_series.trend in ("rising", "volatile"), f"Expected rising HR, got {hr_series.trend}"
    assert hr_series.current_rate is not None
    assert hr_series.current_rate > 0, "HR rate of change should be positive (rising)"


@pytest.mark.asyncio
async def test_velocity_vectors_computed():
    client = FHIRClient()
    engine = TemporalEngine()
    patient = await client.get_patient_profile("test")
    profile = engine.build_profile(patient)

    for code, series in profile.vitals.items():
        if len(series.points) > 2:
            rates = [p.rate_of_change for p in series.points if p.rate_of_change is not None]
            assert len(rates) > 0, f"No rate of change computed for {code}"
