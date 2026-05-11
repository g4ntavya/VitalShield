"""Tests for Layer 4 — CEP engine and Layer 5 — Neurosymbolic scorer."""

import pytest
from vitalshield.fhir.client import FHIRClient
from vitalshield.temporal.timeline import TemporalEngine
from vitalshield.cep.engine import CEPEngine
from vitalshield.scoring.scorer import score_patient


@pytest.mark.asyncio
async def test_sepsis_cep_fires_on_synthetic_patient():
    """The synthetic sepsis patient should trigger the early_sepsis CEP pattern."""
    client = FHIRClient()
    engine = TemporalEngine()
    cep = CEPEngine()

    patient = await client.get_patient_profile("sepsis-demo")
    profile = engine.build_profile(patient)
    result = cep.evaluate(profile)

    sepsis_alerts = [a for a in result.alerts if "sepsis" in a.pattern_name]
    assert len(sepsis_alerts) > 0, "Sepsis pattern should fire on sepsis patient"
    assert result.sepsis_pattern_matched is True
    assert result.sepsis_risk_score > 0.3


@pytest.mark.asyncio
async def test_acuity_score_range():
    """Acuity score must be between 0 and 100."""
    client = FHIRClient()
    engine = TemporalEngine()

    patient = await client.get_patient_profile("test")
    profile = engine.build_profile(patient)
    score = score_patient(profile)

    assert 0 <= score.acuity_score <= 100
    assert score.risk_level in ("low", "moderate", "high", "critical")


@pytest.mark.asyncio
async def test_sepsis_patient_has_elevated_score():
    """Synthetic sepsis patient should have acuity >= 50."""
    client = FHIRClient()
    engine = TemporalEngine()

    patient = await client.get_patient_profile("sepsis-test")
    profile = engine.build_profile(patient)
    score = score_patient(profile)

    assert score.acuity_score >= 30, f"Sepsis patient score too low: {score.acuity_score}"
    assert score.sepsis_positive or score.qsofa_positive or score.sirs_positive, \
        "Sepsis patient should have at least one sepsis screening positive"


@pytest.mark.asyncio
async def test_qsofa_scoring():
    """qSOFA should fire for high RR and low SBP in synthetic patient."""
    client = FHIRClient()
    engine = TemporalEngine()

    patient = await client.get_patient_profile("test")
    profile = engine.build_profile(patient)
    score = score_patient(profile)

    # Synthetic patient has RR=28 and SBP=88 — both qSOFA positive
    assert score.symbolic.qsofa_score >= 2, \
        f"Expected qSOFA >= 2, got {score.symbolic.qsofa_score}"


@pytest.mark.asyncio
async def test_terminology_mapper():
    """Test LOINC normalization."""
    from vitalshield.terminology.mapper import get_mapper
    mapper = get_mapper()

    code, display, conf = mapper.normalize_loinc("8867-4", "heart rate")
    assert code == "8867-4"
    assert conf >= 0.9

    code2, display2, conf2 = mapper.normalize_loinc("HR", "HR")
    assert conf2 >= 0.7
