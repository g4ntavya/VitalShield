"""Layer 1 FHIR package init."""
from vitalshield.fhir.client import FHIRClient
from vitalshield.fhir.models import PatientProfile

__all__ = ["FHIRClient", "PatientProfile"]
