"""
Layer 1 — FHIR R4 Data Ingestion: Pydantic models.

These are VitalShield's internal models — independent of fhir.resources
so the rest of the codebase stays clean regardless of FHIR version changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Demographics ────────────────────────────────────────────────────────────────

class Demographics(BaseModel):
    patient_id: str
    name: str
    birth_date: str | None = None
    gender: str | None = None
    age_years: int | None = None
    chief_complaint: str | None = None


# ── Observations ───────────────────────────────────────────────────────────────

class VitalReading(BaseModel):
    """A single vital sign reading."""
    code: str                      # canonical LOINC code
    display: str                   # human-readable name
    value: float
    unit: str
    timestamp: datetime
    source_code: str = ""          # original code pre-normalization
    source_system: str = ""        # original coding system
    confidence: float = 1.0        # 0-1 data quality score
    is_interpolated: bool = False


class LabResult(BaseModel):
    """A single lab result."""
    code: str                      # canonical LOINC code
    display: str
    value: float
    unit: str
    reference_low: float | None = None
    reference_high: float | None = None
    timestamp: datetime
    source_code: str = ""
    confidence: float = 1.0


# ── Clinical Context ───────────────────────────────────────────────────────────

class Condition(BaseModel):
    code: str                      # canonical SNOMED CT code
    display: str
    clinical_status: str           # active | resolved | inactive
    onset_date: datetime | None = None
    source_code: str = ""


class Medication(BaseModel):
    code: str
    display: str
    dosage: str | None = None
    route: str | None = None
    frequency: str | None = None
    status: str = "active"


class Encounter(BaseModel):
    encounter_id: str
    class_: str = Field(alias="class_", default="unknown")  # IMP, AMB, ED
    status: str
    start: datetime | None = None
    end: datetime | None = None
    type_display: str | None = None

    model_config = {"populate_by_name": True}


# ── Full Patient Profile ────────────────────────────────────────────────────────

class PatientProfile(BaseModel):
    """Complete patient data pulled from FHIR."""
    patient_id: str
    demographics: Demographics
    vitals: list[VitalReading] = Field(default_factory=list)
    labs: list[LabResult] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    medications: list[Medication] = Field(default_factory=list)
    encounters: list[Encounter] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    fhir_server: str = ""
    data_quality_score: float = 1.0   # overall quality 0-1
