"""
Layer 1 — FHIR R4 Data Ingestion: Async FHIR client.

Wraps fhirpy for async FHIR R4 access. Falls back to local Synthea
FHIR JSON bundles when DEMO_MODE=true.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from vitalshield.config import get_settings
from vitalshield.fhir.models import (
    Condition,
    Demographics,
    Encounter,
    LabResult,
    Medication,
    PatientProfile,
    VitalReading,
)

log = logging.getLogger(__name__)

# ── LOINC codes that classify as "vitals" vs "labs" ───────────────────────────
VITAL_LOINC_CODES = {
    "8867-4",   # Heart rate
    "8480-6",   # Systolic blood pressure
    "8462-4",   # Diastolic blood pressure
    "76536-0",  # Mean arterial pressure
    "8310-5",   # Body temperature
    "9279-1",   # Respiratory rate
    "2708-6",   # SpO2 (oxygen saturation)
    "2710-2",   # SpO2 pulse ox
    "59408-5",  # SpO2 pulse ox
    "72514-3",  # Glasgow coma scale
}


class FHIRClient:
    """
    Async FHIR R4 client.

    In DEMO_MODE reads local Synthea FHIR JSON bundles.
    In prod mode uses fhirpy against a real FHIR server.
    """

    def __init__(self, base_url: str | None = None, access_token: str | None = None):
        settings = get_settings()
        self.base_url = base_url or settings.fhir_base_url
        self.access_token = access_token or settings.fhir_access_token
        self.demo_data_path = settings.demo_data_path
        
        # Smart SHARP detection: If a base_url was injected by the platform, 
        # disable demo_mode even if it's set to true in settings.
        if base_url:
            self.demo_mode = False
        else:
            self.demo_mode = settings.demo_mode

    # ── Public API ─────────────────────────────────────────────────────────────

    async def get_patient_profile(self, patient_id: str) -> PatientProfile:
        """Fetch complete patient profile from FHIR server or local demo data."""
        if self.demo_mode:
            return await self._load_demo_patient(patient_id)
        return await self._fetch_live_patient(patient_id)

    async def get_patient_observations(
        self,
        patient_id: str,
        hours_back: int = 72,
    ) -> list[dict]:
        """Fetch all observations for a patient within the given window."""
        if self.demo_mode:
            profile = await self._load_demo_patient(patient_id)
            all_obs = profile.vitals + profile.labs
            cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_back)
            return [
                o.model_dump()
                for o in all_obs
                if o.timestamp.replace(tzinfo=timezone.utc) >= cutoff
            ]
        return await self._fetch_observations_live(patient_id, hours_back)

    # ── Demo Mode ──────────────────────────────────────────────────────────────

    async def _load_demo_patient(self, patient_id: str) -> PatientProfile:
        """Load patient from live HAPI demo server, or local files."""
        demo_path = Path(self.demo_data_path)
        bundle_file: Path | None = None

        # Try exact match
        candidate = demo_path / f"{patient_id}.json"
        if candidate.exists():
            bundle_file = candidate
        else:
            # Fall back to first .json found
            bundles = list(demo_path.glob("*.json"))
            if bundles:
                bundle_file = bundles[0]
                log.warning(
                    "patient_id %s not found; using demo bundle %s",
                    patient_id,
                    bundle_file.name,
                )

        if bundle_file is None:
            log.warning("No demo data found at %s — returning synthetic patient", demo_path)
            return self._synthetic_fallback_patient(patient_id)

        with open(bundle_file) as f:
            bundle = json.load(f)

        return self._parse_fhir_bundle(patient_id, bundle, str(bundle_file))

    def _parse_fhir_bundle(
        self, patient_id: str, bundle: dict, source: str
    ) -> PatientProfile:
        """Parse a FHIR R4 Bundle dict into a PatientProfile."""
        entries = bundle.get("entry", [])
        demographics = Demographics(patient_id=patient_id, name=f"Patient {patient_id}")
        vitals: list[VitalReading] = []
        labs: list[LabResult] = []
        conditions: list[Condition] = []
        medications: list[Medication] = []
        encounters: list[Encounter] = []

        for entry in entries:
            resource = entry.get("resource", {})
            rtype = resource.get("resourceType", "")

            if rtype == "Patient":
                demographics = self._parse_patient(patient_id, resource)
            elif rtype == "Observation":
                obs = self._parse_observation(resource)
                if obs:
                    code = obs.get("code", "")
                    if code in VITAL_LOINC_CODES:
                        vitals.append(VitalReading(**obs))
                    else:
                        labs.append(LabResult(**obs))
            elif rtype == "Encounter":
                enc = self._parse_encounter(resource)
                if enc:
                    encounters.append(Encounter(**enc))
                    # Extract chief complaint if not already set
                    if not demographics.chief_complaint:
                        reasons = resource.get("reasonCode", [])
                        if reasons:
                            demographics.chief_complaint = reasons[0].get("text", reasons[0].get("coding", [{}])[0].get("display", "Complex Presentation"))
            elif rtype == "Condition":
                cond = self._parse_condition(resource)
                if cond:
                    conditions.append(Condition(**cond))
            elif rtype == "MedicationRequest":
                med = self._parse_medication(resource)
                if med:
                    medications.append(Medication(**med))

        # Ensure all timestamps are timezone-aware (UTC) before sorting
        from datetime import timezone
        for v in vitals:
            if v.timestamp.tzinfo is None:
                v.timestamp = v.timestamp.replace(tzinfo=timezone.utc)
        for l in labs:
            if l.timestamp.tzinfo is None:
                l.timestamp = l.timestamp.replace(tzinfo=timezone.utc)

        # Sort vitals/labs by timestamp descending
        vitals.sort(key=lambda x: x.timestamp, reverse=True)
        labs.sort(key=lambda x: x.timestamp, reverse=True)
        
        # DEMONSTRATION TIME SHIFT: 
        # Shift all historical records forward in time so the most recent observation occurs "now". 
        # This allows 5-year-old messy demo FHIR files to work perfectly with the 72-hour sliding window.
        from datetime import datetime, timezone
        if vitals or labs:
            latest_vital = vitals[0].timestamp if vitals else datetime.min.replace(tzinfo=timezone.utc)
            latest_lab = labs[0].timestamp if labs else datetime.min.replace(tzinfo=timezone.utc)
            actual_latest = max(latest_vital, latest_lab)
            if actual_latest.tzinfo is None:
                actual_latest = actual_latest.replace(tzinfo=timezone.utc)
            
            time_shift = datetime.now(timezone.utc) - actual_latest
            
            for v in vitals:
                v.timestamp += time_shift
            for l in labs:
                l.timestamp += time_shift

        return PatientProfile(
            patient_id=patient_id,
            demographics=demographics,
            vitals=vitals,
            labs=labs,
            conditions=conditions,
            medications=medications,
            encounters=encounters,
            fhir_server=source,
        )

    def _parse_patient(self, patient_id: str, r: dict) -> Demographics:
        name_parts = []
        for name_obj in r.get("name", []):
            given = " ".join(name_obj.get("given", []))
            family = name_obj.get("family", "")
            name_parts.append(f"{given} {family}".strip())
        name = name_parts[0] if name_parts else f"Patient {patient_id}"

        birth_date = r.get("birthDate")
        age = None
        if birth_date:
            try:
                birth_year = int(birth_date[:4])
                age = datetime.now().year - birth_year
            except (ValueError, TypeError):
                pass

        return Demographics(
            patient_id=patient_id,
            name=name,
            birth_date=birth_date,
            gender=r.get("gender"),
            age_years=age,
        )

    def _parse_observation(self, r: dict) -> dict | None:
        """Parse FHIR Observation → dict suitable for VitalReading or LabResult."""
        try:
            code_obj = r.get("code", {})
            codings = code_obj.get("coding", [])
            if not codings:
                return None

            # Prefer LOINC
            code = codings[0].get("code", "unknown")
            display = codings[0].get("display", code_obj.get("text", code))
            system = codings[0].get("system", "")
            for c in codings:
                if "loinc" in c.get("system", "").lower():
                    code = c["code"]
                    display = c.get("display", display)
                    system = c["system"]
                    break

            # Value
            value_qty = r.get("valueQuantity", {})
            value = value_qty.get("value")
            if value is None:
                return None
            unit = value_qty.get("unit", value_qty.get("code", ""))

            # Timestamp
            ts_str = r.get("effectiveDateTime") or r.get("issued", "")
            if not ts_str:
                return None
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

            return {
                "code": code,
                "display": display,
                "value": float(value),
                "unit": unit,
                "timestamp": ts,
                "source_code": code,
                "source_system": system,
                "confidence": 1.0,
            }
        except Exception as e:
            log.debug("Skipping observation parse error: %s", e)
            return None

    def _parse_condition(self, r: dict) -> dict | None:
        try:
            code_obj = r.get("code", {})
            codings = code_obj.get("coding", [])
            if not codings:
                return None
            code = codings[0].get("code", "unknown")
            display = codings[0].get("display", code_obj.get("text", code))

            status_obj = r.get("clinicalStatus", {})
            status_codings = status_obj.get("coding", [])
            status = status_codings[0].get("code", "unknown") if status_codings else "unknown"

            onset = r.get("onsetDateTime")
            onset_dt = None
            if onset:
                onset_dt = datetime.fromisoformat(onset.replace("Z", "+00:00"))

            return {
                "code": code,
                "display": display,
                "clinical_status": status,
                "onset_date": onset_dt,
                "source_code": code,
            }
        except Exception as e:
            log.debug("Skipping condition parse error: %s", e)
            return None

    def _parse_medication(self, r: dict) -> dict | None:
        try:
            med_concept = r.get("medicationCodeableConcept", {})
            codings = med_concept.get("coding", [])
            code = codings[0].get("code", "unknown") if codings else "unknown"
            display = codings[0].get("display", med_concept.get("text", code)) if codings else med_concept.get("text", code)

            status = r.get("status", "active")
            return {
                "code": code,
                "display": display,
                "status": status,
            }
        except Exception as e:
            log.debug("Skipping medication parse error: %s", e)
            return None

    def _parse_encounter(self, r: dict) -> dict | None:
        try:
            enc_id = r.get("id", "unknown")
            class_obj = r.get("class", {})
            class_code = class_obj.get("code", "unknown")
            status = r.get("status", "unknown")
            period = r.get("period", {})

            start = None
            if period.get("start"):
                start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            end = None
            if period.get("end"):
                end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))

            type_list = r.get("type", [])
            type_display = None
            if type_list:
                codings = type_list[0].get("coding", [])
                if codings:
                    type_display = codings[0].get("display")

            return {
                "encounter_id": enc_id,
                "class_": class_code,
                "status": status,
                "start": start,
                "end": end,
                "type_display": type_display,
            }
        except Exception as e:
            log.debug("Skipping encounter parse error: %s", e)
            return None

    def _synthetic_fallback_patient(self, patient_id: str) -> PatientProfile:
        """
        Returns a hardcoded synthetic sepsis patient when no real demo data found.
        Useful for unit tests and CI.
        """
        now = datetime.now(timezone.utc)

        def ts(hours_ago: float) -> datetime:
            return now - timedelta(hours=hours_ago)

        vitals = [
            # Heart rate — rising (sepsis pattern)
            VitalReading(code="8867-4", display="Heart rate", value=88, unit="bpm", timestamp=ts(72)),
            VitalReading(code="8867-4", display="Heart rate", value=92, unit="bpm", timestamp=ts(60)),
            VitalReading(code="8867-4", display="Heart rate", value=98, unit="bpm", timestamp=ts(48)),
            VitalReading(code="8867-4", display="Heart rate", value=105, unit="bpm", timestamp=ts(24)),
            VitalReading(code="8867-4", display="Heart rate", value=115, unit="bpm", timestamp=ts(12)),
            VitalReading(code="8867-4", display="Heart rate", value=124, unit="bpm", timestamp=ts(1)),

            # Systolic BP — falling
            VitalReading(code="8480-6", display="Systolic blood pressure", value=128, unit="mm[Hg]", timestamp=ts(72)),
            VitalReading(code="8480-6", display="Systolic blood pressure", value=120, unit="mm[Hg]", timestamp=ts(48)),
            VitalReading(code="8480-6", display="Systolic blood pressure", value=108, unit="mm[Hg]", timestamp=ts(24)),
            VitalReading(code="8480-6", display="Systolic blood pressure", value=95, unit="mm[Hg]", timestamp=ts(6)),
            VitalReading(code="8480-6", display="Systolic blood pressure", value=88, unit="mm[Hg]", timestamp=ts(1)),

            # Temperature — spike
            VitalReading(code="8310-5", display="Body temperature", value=36.9, unit="Cel", timestamp=ts(72)),
            VitalReading(code="8310-5", display="Body temperature", value=37.4, unit="Cel", timestamp=ts(48)),
            VitalReading(code="8310-5", display="Body temperature", value=38.1, unit="Cel", timestamp=ts(24)),
            VitalReading(code="8310-5", display="Body temperature", value=39.2, unit="Cel", timestamp=ts(6)),
            VitalReading(code="8310-5", display="Body temperature", value=39.7, unit="Cel", timestamp=ts(1)),

            # SpO2 — declining
            VitalReading(code="59408-5", display="Oxygen saturation", value=98, unit="%", timestamp=ts(72)),
            VitalReading(code="59408-5", display="Oxygen saturation", value=96, unit="%", timestamp=ts(48)),
            VitalReading(code="59408-5", display="Oxygen saturation", value=94, unit="%", timestamp=ts(24)),
            VitalReading(code="59408-5", display="Oxygen saturation", value=91, unit="%", timestamp=ts(6)),
            VitalReading(code="59408-5", display="Oxygen saturation", value=89, unit="%", timestamp=ts(1)),

            # Respiratory rate — elevated
            VitalReading(code="9279-1", display="Respiratory rate", value=16, unit="/min", timestamp=ts(72)),
            VitalReading(code="9279-1", display="Respiratory rate", value=18, unit="/min", timestamp=ts(48)),
            VitalReading(code="9279-1", display="Respiratory rate", value=22, unit="/min", timestamp=ts(24)),
            VitalReading(code="9279-1", display="Respiratory rate", value=26, unit="/min", timestamp=ts(6)),
            VitalReading(code="9279-1", display="Respiratory rate", value=28, unit="/min", timestamp=ts(1)),
            
            # Fluid Balance Data
            VitalReading(code="31688-5", display="Fluid balance", value=-200, unit="mL", timestamp=ts(48)),
            VitalReading(code="31688-5", display="Fluid balance", value=-800, unit="mL", timestamp=ts(12)),
        ]

        labs = [
            LabResult(code="2524-7", display="Lactate", value=1.2, unit="mmol/L", timestamp=ts(48)),
            LabResult(code="2524-7", display="Lactate", value=2.8, unit="mmol/L", timestamp=ts(12)),
            LabResult(code="2524-7", display="Lactate", value=4.1, unit="mmol/L", timestamp=ts(2)),
            LabResult(code="6690-2", display="WBC count", value=11000, unit="10*3/uL", timestamp=ts(48)),
            LabResult(code="6690-2", display="WBC count", value=14200, unit="10*3/uL", timestamp=ts(12)),
            # Renal Decline data
            LabResult(code="2160-0", display="Creatinine", value=0.9, unit="mg/dL", timestamp=ts(48)),
            LabResult(code="2160-0", display="Creatinine", value=1.3, unit="mg/dL", timestamp=ts(12)),
            LabResult(code="2160-0", display="Creatinine", value=1.5, unit="mg/dL", timestamp=ts(1)),
            LabResult(code="3094-0", display="BUN", value=18, unit="mg/dL", timestamp=ts(48)),
            LabResult(code="3094-0", display="BUN", value=26, unit="mg/dL", timestamp=ts(1)),
        ]

        return PatientProfile(
            patient_id=patient_id,
            demographics=Demographics(
                patient_id=patient_id,
                name="John D.",
                birth_date="1957-03-15",
                gender="male",
                age_years=67,
            ),
            vitals=vitals,
            labs=labs,
            conditions=[
                Condition(
                    code="91302008",
                    display="Sepsis (disorder)",
                    clinical_status="active",
                ),
            ],
            fhir_server="synthetic_fallback",
        )

    # ── Live FHIR Mode ─────────────────────────────────────────────────────────

    async def _fetch_live_patient(self, patient_id: str) -> PatientProfile:
        """Fetch from a live FHIR R4 server using httpx."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=30) as client:
            # Fetch patient
            resp = await client.get(f"/Patient/{patient_id}")
            resp.raise_for_status()
            patient_resource = resp.json()

            # Fetch observations
            obs_resp = await client.get(
                f"/Observation",
                params={"patient": patient_id, "_count": 200, "_sort": "-date"},
            )
            obs_resp.raise_for_status()
            obs_bundle = obs_resp.json()

            # Fetch conditions
            cond_resp = await client.get(
                "/Condition",
                params={"patient": patient_id, "clinical-status": "active"},
            )
            cond_resp.raise_for_status()
            cond_bundle = cond_resp.json()

        # Build a synthetic bundle for unified parsing
        entries = [{"resource": patient_resource}]
        for entry in obs_bundle.get("entry", []):
            entries.append(entry)
        for entry in cond_bundle.get("entry", []):
            entries.append(entry)

        bundle = {"resourceType": "Bundle", "entry": entries}
        return self._parse_fhir_bundle(patient_id, bundle, self.base_url)

    async def _fetch_observations_live(
        self, patient_id: str, hours_back: int
    ) -> list[dict]:
        """Fetch observations directly from live FHIR server."""
        raise NotImplementedError("Live FHIR observation fetch not yet implemented")
