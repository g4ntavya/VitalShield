"""
VitalShield test fixtures using the built-in synthetic patient.
"""

import pytest
import asyncio
from vitalshield.fhir.client import FHIRClient
from vitalshield.temporal.timeline import TemporalEngine
from vitalshield.cep.engine import CEPEngine
from vitalshield.scoring.scorer import score_patient


@pytest.fixture
def synthetic_patient_id():
    return "synthetic-sepsis-demo"


@pytest.fixture
async def patient_profile(synthetic_patient_id):
    client = FHIRClient()
    return await client.get_patient_profile(synthetic_patient_id)


@pytest.fixture
def temporal_engine():
    return TemporalEngine()


@pytest.fixture
async def temporal_profile(patient_profile, temporal_engine):
    return temporal_engine.build_profile(patient_profile)


@pytest.fixture
def cep_engine():
    return CEPEngine()
