import httpx
import json
import os
from pathlib import Path

BASE = "data/synthea/fhir"
os.makedirs(BASE, exist_ok=True)

print("Fetching fresh multi-patient dataset...")
for i in range(5):
    offset = i * 10
    url = f"http://hapi.fhir.org/baseR4/Patient?_has:Observation:patient:code=8867-4&_revinclude=Observation:patient&_revinclude=Condition:patient&_count=1&_getpagesoffset={offset}"
    r = httpx.get(url, timeout=15)
    bundle = r.json()
    if "entry" in bundle:
        # Simplistically find the patient ID
        pid = "unknown"
        for e in bundle["entry"]:
            if e["resource"]["resourceType"] == "Patient":
                pid = e["resource"]["id"]
                break
        
        with open(f"{BASE}/{pid}.json", "w") as f:
            json.dump(bundle, f)
        print(f"Downloaded patient: {pid}")

print(f"Dataset fixed! {len(os.listdir(BASE))} patients available in {BASE}")
