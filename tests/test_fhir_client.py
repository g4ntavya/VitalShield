"""Tests for Layer 1 — FHIR data ingestion."""

import pytest
from vitalshield.fhir.client import FHIRClient


@pytest.mark.asyncio
async def test_fhir_client_returns_patient():
    client = FHIRClient()
    patient = await client.get_patient_profile("test-patient-001")
    assert patient.patient_id == "test-patient-001"
    assert patient.demographics is not None
    assert len(patient.vitals) > 0


@pytest.mark.asyncio
async def test_synthetic_patient_has_sepsis_vitals():
    client = FHIRClient()
    patient = await client.get_patient_profile("any-id")
    hr_readings = [v for v in patient.vitals if v.code == "8867-4"]
    assert len(hr_readings) >= 5, "Synthetic patient should have multiple HR readings"
    # Last HR should be elevated (sepsis pattern)
    last_hr = sorted(hr_readings, key=lambda x: x.timestamp)[-1]
    assert last_hr.value > 110, "Synthetic sepsis patient should have elevated HR"


@pytest.mark.asyncio
async def test_synthetic_patient_has_lactate():
    client = FHIRClient()
    patient = await client.get_patient_profile("any-id")
    lactate = [l for l in patient.labs if l.code == "2524-7"]
    assert len(lactate) > 0, "Should have lactate readings"
