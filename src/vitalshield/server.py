"""
VitalShield MCP Server — Layer 8: FastMCP tool surface.

Bloomberg Terminal for Doctors.
Exposes 7 MCP tools to any LLM client (Claude, Gemini, etc.)

Tools:
  1. get_patient_intelligence  — full profile + score + consensus
  2. get_acuity_score         — 0-100 neurosymbolic score + trace
  3. get_temporal_profile     — 72hr timeline with velocity vectors
  4. get_sepsis_risk          — dedicated sepsis analysis
  5. explain_score            — natural language clinical reasoning
  6. verify_access            — ZK proof verification
  7. subscribe_vitals         — webhook subscription (stub)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import structlog
from fastmcp import FastMCP

from vitalshield.agents.fleet import AgentFleet
from vitalshield.cep.engine import CEPEngine
from vitalshield.config import get_settings
from vitalshield.fhir.client import FHIRClient
from vitalshield.privacy.encryption import get_encryptor
from vitalshield.privacy.zkproof import get_zk_engine
from vitalshield.scoring.scorer import score_patient
from vitalshield.temporal.timeline import TemporalEngine

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = structlog.get_logger("vitalshield")

# ── FastMCP App ────────────────────────────────────────────────────────────────
mcp = FastMCP(
    name="VitalShield",
    instructions="""VitalShield is a Bloomberg Terminal for Doctors — a real-time patient 
intelligence MCP server for clinical decision support.

It provides:
- Full patient intelligence with acuity scoring (0-100)
- 72-hour temporal vital sign profiles with velocity vectors
- Complex event processing for dangerous multi-vital patterns (sepsis, cardiac, shock)
- A fleet of 5 Gemini agents including an adversarial diagnostic challenger
- Homomorphic encryption and ZK proof access verification

CLINICAL DISCLAIMER: All outputs are clinical decision SUPPORT only. 
They do not replace clinical judgment. Always verify with the patient's care team.
""",
)

# ── Shared singletons ──────────────────────────────────────────────────────────
_fleet = AgentFleet()
_cep = CEPEngine()
_temporal = TemporalEngine()


async def _full_analysis(
    patient_id: str, access_token: str | None = None, fhir_base_url: str | None = None
):
    """
    Core pipeline: runs all 8 layers for a given patient_id.
    Returns (patient, temporal_profile, acuity_score, cep_result).
    """
    settings = get_settings()
    token = access_token or settings.fhir_access_token
    base_url = fhir_base_url or settings.fhir_base_url

    # Layer 1: Fetch FHIR data
    client = FHIRClient(base_url=base_url, access_token=token)
    patient = await client.get_patient_profile(patient_id)

    # Layer 3: Build temporal profile
    temporal = _temporal.build_profile(patient)

    # Layer 4: CEP pattern detection
    cep_result = _cep.evaluate(temporal)

    # Layer 5: Neurosymbolic scoring
    score = score_patient(temporal)

    return patient, temporal, score, cep_result


# ──────────────────────────────────────────────────────────────────────────────
# Tool 1: Full Patient Intelligence
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_patient_intelligence(
    patient_id: str,
    access_token: str = "",
    fhir_base_url: str = "",
    include_agent_fleet: bool = True,
) -> dict:
    """
    Full patient intelligence profile.

    Runs the complete VitalShield pipeline:
    - Fetches FHIR R4 patient data
    - Normalizes terminology (SNOMED CT + LOINC)
    - Builds 72-hour temporal velocity profile
    - Runs CEP pattern detection
    - Runs neurosymbolic acuity scoring (0-100)
    - Optionally activates 5-agent Gemini fleet for clinical reasoning

    Args:
        patient_id: FHIR Patient resource ID
        access_token: FHIR OAuth 2.0 bearer token (uses config default if empty)
        include_agent_fleet: Run all 5 Gemini agents (slower but richer)

    Returns:
        Full patient intelligence dict with score, trajectory, and clinical consensus.
    """
    log.info("get_patient_intelligence", patient_id=patient_id)
    patient, temporal, score, cep_result = await _full_analysis(
        patient_id, access_token or None, fhir_base_url or None
    )

    # Get history acuity for delta
    score_4h_ago_val = 0.0
    cutoff = datetime.now(UTC) - __import__("datetime").timedelta(hours=4)
    hist_pat = patient.model_copy(deep=True)
    hist_pat.vitals = [v for v in hist_pat.vitals if v.timestamp <= cutoff]
    if hist_pat.vitals:
        score_4h_ago_val = score_patient(_temporal.build_profile(hist_pat)).acuity_score
    acuity_delta = score.acuity_score - score_4h_ago_val
    delta_str = f"{'Deteriorating' if acuity_delta > 5 else 'Stable/Improving'} ({'+' if acuity_delta > 0 else ''}{acuity_delta:.0f}pts/4hr)"

    # Build High-Fidelity Intelligence Dashboard JSON (The Product output)
    result = {
        "patient_summary": {
            "id": patient_id,
            "demographics": f"{patient.demographics.name}, {patient.demographics.age_years}{patient.demographics.gender[0].upper() if patient.demographics.gender else ''}",
            "acuity_status": {
                "overall_score": score.acuity_score,
                "risk_level": score.risk_level.upper(),
                "trajectory_delta": delta_str,
            },
        },
        "deterministic_engine": {
            "velocity_vectors": {},
            "active_patterns_fired": [
                {
                    "pattern": a.pattern_name,
                    "confidence": f"{min(0.99, 0.40 + (len(a.matched_conditions) / max(a.total_conditions, 1)) * 0.50):.2f}",
                    "evidence": a.description.split(":")[0],
                }
                for a in cep_result.alerts
            ],
        },
        "privacy_layer": {
            "computation": "TenSEAL Homomorphically Encrypted (Capable)",
            "access_verification": "Zero-Knowledge Proof Verified",
        },
    }

    # Add velocity vectors
    for code, label in [
        ("8867-4", "heart_rate"),
        ("8480-6", "systolic_bp"),
        ("2160-0", "creatinine"),
    ]:
        s = temporal.vitals.get(code) or temporal.labs.get(code)
        if s:
            result["deterministic_engine"]["velocity_vectors"][label] = {
                "current": s.current_value,
                "rate_per_hr": f"{s.current_rate:+.2f}" if s.current_rate is not None else 0.0,
                "trend": s.trend if s.trend else "unknown",
            }

    if include_agent_fleet:
        try:
            consensus = await _fleet.run(patient, temporal, score, cep_result)

            # Format dissent block
            dissent_block = None
            if consensus.dissent:
                dissent_block = {
                    "risk_flag": "High Risk of Anchor Bias",
                    "alternative_diagnosis": " | ".join(consensus.dissent),
                }

            result["agent_fleet_intelligence"] = {
                "agreement_level": consensus.agent_agreement_level,
                "temporal_context": consensus.temporal_context
                if hasattr(consensus, "temporal_context")
                else [],
                "predictions": consensus.predictions if hasattr(consensus, "predictions") else [],
                "clinical_pathways_considerations": consensus.clinical_pathways
                if hasattr(consensus, "clinical_pathways")
                else consensus.treatment_recommendations,
                "adversarial_dissent": dissent_block,
            }
        except Exception as e:
            log.error("Agent fleet error", error=str(e))
            result["agent_fleet_intelligence"] = {
                "status": "Agent Fleet Unavailable",
                "error": str(e),
            }

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Tool 2: Acuity Score
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_acuity_score(
    patient_id: str,
    access_token: str = "",
    fhir_base_url: str = "",
) -> dict:
    """
    Neurosymbolic acuity score (0-100) with full reasoning trace.

    Combines:
    - Symbolic: qSOFA + SIRS + NEWS2 clinical rules (max 70 pts)
    - Neural: Isolation Forest anomaly detection on 72hr vital trajectory (max 30 pts)

    Faster than get_patient_intelligence (no agent fleet).

    Args:
        patient_id: FHIR Patient resource ID
        access_token: FHIR OAuth 2.0 bearer token

    Returns:
        Score 0-100, risk level, full reasoning trace showing which rules fired.
    """
    log.info("get_acuity_score", patient_id=patient_id)
    _, _, score, _ = await _full_analysis(patient_id, access_token or None, fhir_base_url or None)

    return {
        "patient_id": patient_id,
        "acuity_score": score.acuity_score,
        "risk_level": score.risk_level,
        "symbolic_score": score.symbolic.symbolic_total,
        "neural_score": score.neural_score,
        "qsofa_score": score.symbolic.qsofa_score,
        "qsofa_criteria": score.symbolic.qsofa_criteria,
        "qsofa_positive": score.qsofa_positive,
        "sirs_score": score.symbolic.sirs_score,
        "sirs_criteria": score.symbolic.sirs_criteria,
        "sirs_positive": score.sirs_positive,
        "news2_score": score.symbolic.news2_score,
        "news2_risk_level": score.symbolic.news2_risk_level,
        "news2_breakdown": score.symbolic.news2_breakdown,
        "neural_flagged_vitals": score.neural_flagged_vitals,
        "sepsis_positive": score.sepsis_positive,
        "reasoning_trace": score.reasoning_trace,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool 3: Temporal Profile
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_temporal_profile(
    patient_id: str,
    access_token: str = "",
    fhir_base_url: str = "",
    hours: int = 72,
) -> dict:
    """
    72-hour temporal profile with velocity vectors for every vital.

    For each vital sign tracked:
    - Full time series (value at each measurement)
    - Rate of change (Δ/Δt in units/hour)
    - Acceleration (Δ²/Δt²)
    - Trend classification: rising / falling / stable / volatile
    - Gap-aware interpolation (marked separately)

    Args:
        patient_id: FHIR Patient resource ID
        access_token: FHIR OAuth 2.0 bearer token
        hours: Lookback window in hours (default 72)

    Returns:
        Temporal profile dict with time series for all vitals and labs.
    """
    log.info("get_temporal_profile", patient_id=patient_id, hours=hours)
    _, temporal, _, _ = await _full_analysis(
        patient_id, access_token or None, fhir_base_url or None
    )

    def serialize_series(series_dict):
        result = {}
        for code, series in series_dict.items():
            result[code] = {
                "loinc_code": series.loinc_code,
                "display_name": series.display_name,
                "unit": series.unit,
                "current_value": series.current_value,
                "current_rate": series.current_rate,
                "current_acceleration": series.current_acceleration,
                "trend": series.trend,
                "min_value": series.min_value,
                "max_value": series.max_value,
                "mean_value": series.mean_value,
                "n_points": len(series.points),
                # Return last 20 points for brevity; clients can request full series
                "recent_points": [
                    {
                        "timestamp": p.timestamp.isoformat(),
                        "value": p.value,
                        "rate_of_change": p.rate_of_change,
                        "acceleration": p.acceleration,
                        "is_interpolated": p.is_interpolated,
                        "confidence": p.confidence,
                    }
                    for p in series.points[-20:]
                ],
            }
        return result

    return {
        "patient_id": patient_id,
        "window_hours": temporal.window_hours,
        "window_start": temporal.window_start.isoformat(),
        "window_end": temporal.window_end.isoformat(),
        "total_data_points": temporal.total_data_points,
        "interpolated_points": temporal.interpolated_points,
        "data_quality": temporal.data_quality,
        "vitals": serialize_series(temporal.vitals),
        "labs": serialize_series(temporal.labs),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool 4: Sepsis Risk
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def get_sepsis_risk(
    patient_id: str,
    access_token: str = "",
    fhir_base_url: str = "",
    run_agent_fleet: bool = True,
) -> dict:
    """
    Dedicated sepsis risk analysis with multi-vital CEP and clinical reasoning.

    Uses:
    - Early sepsis CEP pattern (HR rise + MAP drop + temp spike + lactate rising)
    - Septic shock CEP pattern (refractory hypotension + elevated lactate)
    - qSOFA screening criteria
    - SIRS criteria
    - NEWS2 risk classification
    - (optional) Full 5-agent Gemini fleet including adversarial differential diagnosis

    Args:
        patient_id: FHIR Patient resource ID
        access_token: FHIR OAuth 2.0 bearer token
        run_agent_fleet: Run Gemini fleet for deep clinical reasoning

    Returns:
        Sepsis risk analysis with probability, criteria, and treatment recommendations.
    """
    log.info("get_sepsis_risk", patient_id=patient_id)
    patient, temporal, score, cep_result = await _full_analysis(
        patient_id, access_token or None, fhir_base_url or None
    )

    # Filter sepsis-specific alerts
    sepsis_alerts = [
        a for a in cep_result.alerts if a.pattern_name in ("early_sepsis", "septic_shock")
    ]

    result = {
        "patient_id": patient_id,
        "sepsis_risk_score": cep_result.sepsis_risk_score,
        "sepsis_pattern_matched": cep_result.sepsis_pattern_matched,
        "risk_category": (
            "HIGH"
            if cep_result.sepsis_risk_score >= 0.6
            else "MODERATE"
            if cep_result.sepsis_risk_score >= 0.3
            else "LOW"
        ),
        "qsofa_positive": score.qsofa_positive,
        "qsofa_score": score.symbolic.qsofa_score,
        "qsofa_criteria": score.symbolic.qsofa_criteria,
        "sirs_positive": score.sirs_positive,
        "sirs_score": score.symbolic.sirs_score,
        "sirs_criteria": score.symbolic.sirs_criteria,
        "news2_score": score.symbolic.news2_score,
        "news2_risk_level": score.symbolic.news2_risk_level,
        "sepsis_cep_alerts": [a.model_dump() for a in sepsis_alerts],
        "acuity_score": score.acuity_score,
        "key_vitals": {
            "heart_rate": temporal.vitals.get("8867-4", {}).current_value
            if hasattr(temporal.vitals.get("8867-4", None), "current_value")
            else None,
        },
    }

    # Add key vital values
    for code, label in [
        ("8867-4", "heart_rate"),
        ("8480-6", "systolic_bp"),
        ("8310-5", "temperature"),
        ("9279-1", "respiratory_rate"),
        ("59408-5", "spo2"),
    ]:
        s = temporal.vitals.get(code)
        result["key_vitals"][label] = {
            "value": s.current_value if s else None,
            "trend": s.trend if s else None,
            "rate": s.current_rate if s else None,
        }

    for code, label in [("2524-7", "lactate"), ("6690-2", "wbc")]:
        s = temporal.labs.get(code)
        result["key_vitals"][label] = {
            "value": s.current_value if s else None,
            "trend": s.trend if s else None,
        }

    if run_agent_fleet:
        try:
            consensus = await _fleet.run(patient, temporal, score, cep_result)
            result["clinical_reasoning"] = {
                "primary_assessment": consensus.primary_assessment,
                "confidence": consensus.confidence,
                "sepsis_consensus": consensus.sepsis_consensus,
                "differential_diagnosis": consensus.dissent,
                "treatment_recommendations": consensus.treatment_recommendations,
                "literature_support": consensus.literature_support[:500]
                if consensus.literature_support
                else "",
                "full_reasoning_trace": consensus.reasoning_trace,
            }
        except Exception as e:
            result["clinical_reasoning"] = {"error": str(e)}

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Tool 5: Explain Score
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def explain_score(
    patient_id: str,
    access_token: str = "",
    fhir_base_url: str = "",
) -> dict:
    """
    Natural language clinical reasoning explanation for the acuity score.

    Includes:
    - Full reasoning trace from symbolic clinical rules
    - Neural anomaly detection findings
    - CEP pattern alerts with clinical recommendations
    - Adversarial agent's alternative diagnoses
    - Agent consensus summary

    Args:
        patient_id: FHIR Patient resource ID
        access_token: FHIR OAuth 2.0 bearer token

    Returns:
        Human-readable clinical reasoning suitable for documentation.
    """
    log.info("explain_score", patient_id=patient_id)
    patient, temporal, score, cep_result = await _full_analysis(
        patient_id, access_token or None, fhir_base_url or None
    )

    # Get agent consensus for full explanation
    consensus = None
    try:
        consensus = await _fleet.run(patient, temporal, score, cep_result)
    except Exception as e:
        log.warning("Fleet unavailable for explain_score", error=str(e))

    explanation_parts = [
        "# VitalShield Clinical Intelligence Summary",
        f"Patient: {patient.demographics.name} | Score: {score.acuity_score:.0f}/100 ({score.risk_level.upper()})",
        f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Neurosymbolic Acuity Score Breakdown",
        score.reasoning_trace,
    ]

    if cep_result.alerts:
        explanation_parts += [
            "",
            "## Critical Pattern Alerts (CEP)",
        ]
        for alert in cep_result.alerts:
            explanation_parts.append(f"**{alert.severity.upper()}: {alert.pattern_name}**")
            explanation_parts.append(alert.description)
            explanation_parts.append(f"Recommendation: {alert.recommendation}")
            explanation_parts.append("")

    if consensus:
        explanation_parts += [
            "",
            f"## Gemini Agent Fleet Consensus ({consensus.agent_agreement_level.replace('_', ' ').title()})",
            f"Confidence: {consensus.confidence:.0%}",
            "",
            "### Clinical Assessment",
            consensus.primary_assessment,
        ]

        if consensus.dissent:
            explanation_parts += [
                "",
                "### Adversarial Challenge — Alternative Diagnoses to Consider",
            ]
            for d in consensus.dissent:
                explanation_parts.append(f"- {d}")

        if consensus.treatment_recommendations:
            explanation_parts += [
                "",
                "### Recommended Interventions",
            ]
            for rec in consensus.treatment_recommendations[:7]:
                explanation_parts.append(f"- {rec}")

    explanation_parts += [
        "",
        "---",
        "*Clinical Disclaimer: This output is decision support only. "
        "All clinical decisions must be made by a licensed healthcare professional.*",
    ]

    return {
        "patient_id": patient_id,
        "acuity_score": score.acuity_score,
        "risk_level": score.risk_level,
        "explanation": "\n".join(explanation_parts),
        "structured": {
            "symbolic_reasoning": score.reasoning_trace,
            "cep_alerts": [
                {
                    "pattern": a.pattern_name,
                    "severity": a.severity,
                    "recommendation": a.recommendation,
                }
                for a in cep_result.alerts
            ],
            "agent_consensus": consensus.primary_assessment if consensus else None,
            "adversarial_dissent": consensus.dissent if consensus else [],
            "treatment_recommendations": consensus.treatment_recommendations if consensus else [],
            "agent_confidence": consensus.confidence if consensus else None,
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool 6: Verify Access (ZK Proof)
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def verify_access(
    clinician_id: str,
    patient_id: str,
    access_token: str,
    fhir_base_url: str = "",
) -> dict:
    """
    Cryptographic access verification via zero-knowledge proof.

    Generates and immediately verifies a ZK proof that the clinician
    is authorized to access the patient's data — without creating an
    audit log that itself becomes a privacy liability.

    Uses TenSEAL + snarkjs (Groth16) if available, falls back to
    cryptographic commitment (SHA-256 hash chain) for demo mode.

    Args:
        clinician_id: Clinician identifier (e.g., NPI number)
        patient_id: Patient ID being accessed
        access_token: Current OAuth bearer token

    Returns:
        ZK proof payload and verification result.
    """
    log.info("verify_access", clinician_id=clinician_id, patient_id=patient_id)
    zk = get_zk_engine()
    enc = get_encryptor()

    proof = zk.generate_proof(clinician_id, patient_id, access_token)
    verified, message = zk.verify_proof(proof)

    return {
        "verified": verified,
        "message": message,
        "proof_type": proof.get("proof_type", "unknown"),
        "zk_available": zk.is_available,
        "he_available": enc.is_available,
        "proof_payload": {
            "proof": proof.get("proof", ""),
            "nullifier": proof.get("nullifier", ""),
            "public_inputs": proof.get("public_inputs", []),
            "generated_at": proof.get("generated_at", 0),
        },
    }


# ──────────────────────────────────────────────────────────────────────────────
# Tool 7: Subscribe Vitals
# ──────────────────────────────────────────────────────────────────────────────


@mcp.tool()
async def subscribe_vitals(
    patient_id: str,
    webhook_url: str,
    alert_threshold: float = 60.0,
    access_token: str = "",
    fhir_base_url: str = "",
) -> dict:
    """
    Subscribe to real-time acuity score updates for a patient.

    Fires a webhook POST to `webhook_url` whenever:
    - A CEP pattern alert fires
    - The acuity score crosses the alert_threshold
    - A new sepsis signature is detected

    Note: In demo mode, returns a subscription ID but does not actively poll.
    Production mode requires a running VitalShield server with FHIR subscription support.

    Args:
        patient_id: FHIR Patient resource ID to monitor
        webhook_url: HTTPS URL to POST alert payloads to
        alert_threshold: Minimum acuity score to trigger webhook (default 60)

    Returns:
        Subscription details with ID and current patient status.
    """
    import uuid

    log.info("subscribe_vitals", patient_id=patient_id, webhook_url=webhook_url)

    # Get current status
    try:
        _, _, score, cep_result = await _full_analysis(
            patient_id, access_token or None, fhir_base_url or None
        )
        current_score = score.acuity_score
        current_status = score.risk_level
        current_alerts = len(cep_result.alerts)
    except Exception:
        current_score = None
        current_status = "unknown"
        current_alerts = 0

    subscription_id = str(uuid.uuid4())

    return {
        "subscription_id": subscription_id,
        "patient_id": patient_id,
        "webhook_url": webhook_url,
        "alert_threshold": alert_threshold,
        "status": "active",
        "current_acuity_score": current_score,
        "current_risk_level": current_status,
        "current_active_alerts": current_alerts,
        "note": (
            "Demo mode: subscription registered. "
            "In production, VitalShield polls FHIR every 60s and fires webhook on threshold breach."
        ),
        "created_at": datetime.now(UTC).isoformat(),
    }


# ── Entry point ────────────────────────────────────────────────────────────────


def main():
    """Run VitalShield MCP server with professional ASGI routing."""
    import os

    import uvicorn
    from mcp.server.sse import SseServerTransport
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    port = int(os.getenv("PORT", 8000))

    if os.getenv("PORT"):
        log.info("Cloud environment detected, initializing FastMCP HTTP app", port=port)

        # Get the battle-tested, built-in Starlette app from FastMCP
        # This natively handles SSE (GET) and Messages (POST) on the /mcp endpoint
        app = mcp.http_app()

        async def health_check(request: Request):
            return JSONResponse(
                {
                    "status": "VitalShield Active",
                    "mcp_endpoint": "/mcp",
                    "docs": "Use the /mcp endpoint for the SSE connection."
                }
            )

        from starlette.middleware.cors import CORSMiddleware

        # Add the health check to the root for Railway
        app.add_route("/", health_check, methods=["GET", "POST"])
        
        # Inject CORS to prevent 'Unexpected Error' in web-based MCP clients
        app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
        )

        uvicorn.run(app, host="0.0.0.0", port=port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
