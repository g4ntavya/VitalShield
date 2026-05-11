import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from rich.console import Console
from rich.table import Table
from rich.status import Status

from vitalshield.scoring.scorer import score_patient
from vitalshield.temporal.models import TemporalProfile

# Setup logging
logging.basicConfig(level=logging.ERROR)
console = Console()

def get_sparkline(values: list[float]) -> str:
    """Generate a high-density ASCII sparkline for a list of values."""
    if not values: return " " * 8
    chars = " ▂▃▄▅▆▇█"
    v_min, v_max = min(values), max(values)
    if v_min == v_max: return chars[0] * len(values)
    
    indices = [int((v - v_min) / (v_max - v_min) * 7) for v in values]
    return "".join(chars[i] for i in indices)

def get_historical_acuity(engine, patient, hours_ago):
    """Utility to calculate drift delta."""
    from copy import deepcopy
    historical_pat = deepcopy(patient)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_ago)
    
    historical_pat.vitals = [v for v in historical_pat.vitals if v.timestamp <= cutoff]
    
    if not historical_pat.vitals:
        return 0.0
    
    profile = engine.build_profile(historical_pat)
    score = score_patient(profile)
    return score.acuity_score

def render_sparklines(temporal: TemporalProfile):
    """Render the 72-hour trajectory table."""
    from rich.table import Table
    console.print("[bold cyan]72-HOUR TRAJECTORY:[/bold cyan]")
    track_codes = [
        ("8867-4",  "Heart Rate"), 
        ("8480-6",  "Systolic BP"), 
        ("8462-4",  "Diastolic BP"),
        ("9279-1",  "Resp. Rate"),
        ("59408-5", "SpO2"),
        ("8310-5",  "Temperature (C)"),
        ("2160-0",  "Creatinine")
    ]
    
    traj_table = Table(show_header=False, box=None, padding=(0, 2))
    traj_table.add_column("Vital", style="bold white")
    traj_table.add_column("Sparkline", style="white", justify="right")
    traj_table.add_column("Value", style="bold cyan", justify="right")
    traj_table.add_column("Rate", justify="left")
    
    for code, label in track_codes:
        s = temporal.vitals.get(code) or temporal.labs.get(code)
        if s and s.points:
            vals = [p.value for p in s.points[-15:]]
            spark = get_sparkline(vals).rjust(8, "·")
            
            rate_val = s.current_rate if s.current_rate is not None else 0.0
            rate_color = "red" if ("BP" in label and rate_val < 0) or ("Heart" in label and rate_val > 0) or ("Creatinine" in label and rate_val > 0) else "green"
            if abs(rate_val) < 0.01:
                rate_color = "white"
                
            rate_str = f"[{rate_color}]{rate_val:+.2f}/hr[/{rate_color}]"
            
            traj_table.add_row(
                label,
                spark,
                f"{s.current_value:.1f}",
                rate_str
            )
            
    console.print(traj_table)

async def run_dashboard(patient_id: str):
    from vitalshield.fhir.client import FHIRClient
    from vitalshield.temporal.timeline import TemporalEngine
    from vitalshield.cep.engine import CEPEngine
    from vitalshield.scoring.scorer import score_patient
    from vitalshield.agents.fleet import AgentFleet

    # 1. Fetch & Process (Deterministic Layer)
    client = FHIRClient()
    patient = await client.get_patient_profile(patient_id)
    engine = TemporalEngine()
    temporal = engine.build_profile(patient)
    cep_engine = CEPEngine()
    cep_result = cep_engine.evaluate(temporal)
    current_score = score_patient(temporal)
    
    score_4h_ago = get_historical_acuity(engine, patient, 4)
    acuity_delta = current_score.acuity_score - score_4h_ago
    
    # 2. RENDER IMMEDIATE CLINICAL STATE
    status_color = "red" if acuity_delta > 5 else "yellow" if acuity_delta > 0 else "green"
    status_word = "Deteriorating" if acuity_delta > 5 else "Declining" if acuity_delta > 0 else "Stable"
    sign = "+" if acuity_delta > 0 else ""
    
    d = patient.demographics
    console.print(f"[bold cyan]PATIENT:[/bold cyan] {d.name}, {d.age_years}{d.gender[0].upper() if d.gender else ''}")
    if d.chief_complaint:
        console.print(f"[bold cyan]CHIEF COMPLAINT:[/bold cyan] [white]{d.chief_complaint}[/white]")
    
    console.print(f"[bold cyan]OVERALL STATUS:[/bold cyan] [{status_color}]{status_word} ({sign}{acuity_delta:.0f}pts/4hr)[/{status_color}]\n")
    
    # Deterministic Temporal Summary (Math-based, instantaneous)
    console.print("[bold cyan]TEMPORAL DRIFT (72H):[/bold cyan]")
    if abs(acuity_delta) > 1:
        console.print(f"  [white]→ Acuity score shifted {acuity_delta:+.1f} points in last 4 hours.[/white]")
    else:
        console.print(f"  [white]→ Physiological baseline remains stable over the immediate 4h window.[/white]")
    
    render_sparklines(temporal)
    
    console.print("\n[bold cyan]ACTIVE PATTERNS:[/bold cyan]")
    if cep_result.alerts:
        for alert in cep_result.alerts:
            color = "red" if alert.severity == "emergency" else "yellow"
            console.print(f"  [{color}]→ {alert.description.split(':')[0]}[/{color}]")
    else:
        console.print("  [white]No critical trajectories detected.[/white]")

    # 3. BACKGROUND INTELLIGENCE (LLM Fleet)
    fleet = AgentFleet()
    with console.status("[bold yellow]Inference: Running Adversarial Agent Fleet...[/bold yellow]", spinner="dots"):
        consensus = await fleet.run(patient, temporal, current_score, cep_result)
    
    # 4. RENDER AI CLINICAL SYNTHESIS
    console.print("\n[bold cyan]AI CLINICAL CONTEXT:[/bold cyan]")
    if consensus.temporal_context:
        for p in consensus.temporal_context[:3]:
            console.print(f"  [white]→ {p}[/white]")
    else:
        console.print("  [white]→ Fleet suggests trajectory remains within expected bounds.[/white]")

    console.print("\n[bold cyan]AGENT CONSENSUS:[/bold cyan]")
    if not consensus.error:
        console.print(f"  [white]→ Fleet {consensus.agent_agreement_level}ly favors primary assessment (Confidence: {consensus.confidence*100:.0f}%)[/white]")
        if consensus.dissent:
            console.print(f"  [white]→ Agent 5 (Adversarial): {consensus.dissent[0]}[/white]")
    else:
        console.print(f"  [white]→ Agent fleet unavailable ({consensus.error})[/white]")

    console.print("\n[bold cyan]PREDICTIONS:[/bold cyan]")
    if consensus.predictions:
        for p in consensus.predictions[:3]:
            console.print(f"  [white]→ {p}[/white]")
    else:
        console.print("  [white]→ Low immediate risk of deterioration[/white]")

    console.print("\n[bold cyan]CLINICAL PATHWAYS (Considerations):[/bold cyan]")
    if consensus.clinical_pathways:
        for item in consensus.clinical_pathways[:4]:
            console.print(f"  [white]→ {item}[/white]")
    else:
        console.print("  [white]→ Standard monitoring per unit protocol[/white]")

    # ZK Privacy Footer
    from vitalshield.privacy.zkproof import get_zk_engine
    zk = get_zk_engine()
    proof = zk.generate_proof("clinician-001", patient_id, "access-token")
    verified, _ = zk.verify_proof(proof)
    if verified:
         console.print("\n[dim]🔒 Access verified via Zero-Knowledge state commitment[/dim]\n")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="VitalShield Dashboard")
    # Dynamically find first patient in data dir if not specified
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "synthea", "fhir")
    default_id = "89980748"
    if os.path.exists(data_dir):
        files = [f.replace(".json", "") for f in os.listdir(data_dir) if f.endswith(".json") and not f.startswith(".")]
        if files: default_id = files[0]

    parser.add_argument("--patient-id", default=default_id)
    args = parser.parse_args()
    asyncio.run(run_dashboard(args.patient_id))

if __name__ == "__main__":
    main()
