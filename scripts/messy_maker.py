import json
import random
import copy
from pathlib import Path

def make_it_messy(input_file: str, output_file: str):
    with open(input_file, 'r') as f:
        bundle = json.load(f)
    
    messy_bundle = copy.deepcopy(bundle)
    entries = messy_bundle.get("entry", [])
    
    new_entries = []
    for entry in entries:
        resource = entry.get("resource", {})
        
        # 1. Mess with Observations (vitals/labs)
        if resource.get("resourceType") == "Observation":
            # 20% chance to drop the reading entirely (sensor gap)
            if random.random() < 0.2:
                continue
                
            # Inject noise into numerical values
            if "valueQuantity" in resource:
                val = resource["valueQuantity"]["value"]
                
                # 5% chance of an extreme outlier (glitch)
                if random.random() < 0.05:
                    resource["valueQuantity"]["value"] = val * 5 if random.random() > 0.5 else val * 0.1
                else:
                    # Generic noise (+/- 5%)
                    noise = 1 + (random.uniform(-0.05, 0.05))
                    resource["valueQuantity"]["value"] = val * noise

            # 10% chance to mess up the timestamp format slightly
            if "effectiveDateTime" in resource and random.random() < 0.1:
                ts = resource["effectiveDateTime"]
                resource["effectiveDateTime"] = ts.replace("T", " ")
        
        new_entries.append(entry)

    # 2. Complete Shuffle (EHR data is rarely sorted)
    random.shuffle(new_entries)
    messy_bundle["entry"] = new_entries
    
    with open(output_file, 'w') as f:
        json.dump(messy_bundle, f, indent=2)
    
    print(f"Chaos injected! {len(new_entries)} entries shuffled and corrupted in {output_file}")

if __name__ == "__main__":
    import os
    base = "/Users/gantavya/Desktop/MCP_health"
    make_it_messy(os.path.join(base, "data/synthea/fhir/clean_patient.json"), os.path.join(base, "data/synthea/fhir/messy.json"))
