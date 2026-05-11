import asyncio
import os
import sys
import json
sys.path.insert(0, os.path.abspath("src"))

from vitalshield.server import get_patient_intelligence, verify_access

async def main():
    print("\n--- INPUT DATA ---")
    print("Patient ID: demo-sepsis-001")
    print("Clinician ID: NPI-8472910")
    print("Access Token: eyJhbGciOiJIUzI1NiIsInR...")
    print("FHIR Base URL: https://sharp.hospital.internal/fhir\n")

    # Call verify_access
    print("--- 1. EXECUTING ZERO-KNOWLEDGE PROOF (verify_access) ---")
    zk_result = await verify_access("NPI-8472910", "demo-sepsis-001", "eyJhbGciOiJIUzI1NiIsInR...")
    print(json.dumps(zk_result, indent=2))
    print("\n")

    # Call get_patient_intelligence
    # Note: Using include_agent_fleet=False here to bypass Gemini 400 errors without an API key,
    # or you can test with empty if it gracefully fails.
    print("--- 2. EXECUTING INTELLIGENCE DASHBOARD (get_patient_intelligence) ---")
    dashboard = await get_patient_intelligence(
        patient_id="demo-sepsis-001",
        access_token="eyJhbGciOiJIUzI1NiIsInR...",
        fhir_base_url="https://sharp.hospital.internal/fhir",
        include_agent_fleet=True
    )
    print(json.dumps(dashboard, indent=2))

if __name__ == "__main__":
    asyncio.run(main())
