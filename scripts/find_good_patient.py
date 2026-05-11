import httpx

print("Hunting for a rich FHIR patient...")
for i in range(0, 100, 5):
    url = f"http://hapi.fhir.org/baseR4/Patient?_has:Observation:patient:code=8867-4&_revinclude=Observation:patient&_revinclude=Condition:patient&_count=5&_getpagesoffset={i}"
    r = httpx.get(url, timeout=10)
    bundle = r.json()
    entries = bundle.get("entry", [])
    
    # group by patient ID
    patients = {}
    for e in entries:
        res = e.get("resource", {})
        if res.get("resourceType") == "Patient":
            patients[res.get("id")] = [res]
        elif "patient" in res or "subject" in res:
            ref = res.get("subject", res.get("patient", {})).get("reference", "")
            pid = ref.replace("Patient/", "")
            if pid in patients:
                patients[pid].append(res)
                
    for pid, resources in patients.items():
        obs_count = len([r for r in resources if r.get("resourceType") == "Observation"])
        if obs_count > 30:
            print(f"FOUND RICH PATIENT! ID: {pid} with {obs_count} observations. Downloading...")
            # We will use this patient ID for the live fetch!
            with open("data/synthea/fhir/messy.json", "w") as f:
                import json
                json.dump({"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": r} for r in resources]}, f)
            print("Saved to messy.json")
            exit(0)
print("Could not find a rich patient quickly.")
