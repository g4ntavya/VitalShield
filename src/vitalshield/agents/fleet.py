"""
Layer 6 — Agent Fleet: Parallel Gemini agent orchestrator.

5 agents run concurrently via asyncio.gather:
  1. Triage    (Flash) — rapid severity assessment
  2. Depth     (Pro)   — systematic clinical reasoning
  3. Literature (Flash) — evidence validation  
  4. Treatment  (Pro)   — intervention suggestions
  5. Adversarial (Pro)  — diagnostic challenge

All share a structured patient context string as input.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

try:
    from google import genai
    from google.genai import types as genai_types
    _GENAI_NEW = True
except ImportError:
    import google.generativeai as genai  # legacy fallback
    _GENAI_NEW = False

from vitalshield.agents.models import AgentOutput, ConsensusResult
from vitalshield.agents.prompts import (
    ADVERSARIAL_PROMPT,
    DEPTH_PROMPT,
    LITERATURE_PROMPT,
    TRIAGE_PROMPT,
    TREATMENT_PROMPT,
)
from vitalshield.cep.models import CEPResult
from vitalshield.config import get_settings
from vitalshield.fhir.models import PatientProfile
from vitalshield.scoring.models import AcuityScore
from vitalshield.temporal.models import TemporalProfile

log = logging.getLogger(__name__)

# Gemini model names
_FLASH = "gemini-3-flash-preview"
_PRO   = "gemini-3.1-pro-preview"


def _build_patient_context(
    patient: PatientProfile,
    temporal: TemporalProfile,
    score: AcuityScore,
    cep: CEPResult,
) -> str:
    """Build a rich clinical context string for the agents."""
    d = patient.demographics
    lines = [
        f"=== ANONYMIZED PATIENT CONTEXT ===",
        f"Subject Vector: [Redacted for Privacy]",
        f"Demographics: {d.gender or 'unknown'}, age {d.age_years or '?'}",
        f"",
        f"=== ACUITY SCORE: {score.acuity_score:.0f}/100 ({score.risk_level.upper()}) ===",
        f"qSOFA: {score.symbolic.qsofa_score}/3 ({'POSITIVE' if score.qsofa_positive else 'negative'})",
    ]
    for c in score.symbolic.qsofa_criteria:
        lines.append(f"  ✓ {c}")
    lines.append(f"SIRS: {score.symbolic.sirs_score}/4 ({'POSITIVE' if score.sirs_positive else 'negative'})")
    for c in score.symbolic.sirs_criteria:
        lines.append(f"  ✓ {c}")
    lines.append(f"NEWS2: {score.symbolic.news2_score} ({score.symbolic.news2_risk_level} risk)")
    lines.append(f"")

    # Current vitals
    lines.append("=== CURRENT VITALS ===")
    vital_labels = {
        "8867-4": "Heart Rate",
        "8480-6": "Systolic BP",
        "8462-4": "Diastolic BP",
        "76536-0": "Mean Art. Pressure",
        "8310-5": "Temperature",
        "9279-1": "Respiratory Rate",
        "59408-5": "SpO2",
        "72514-3": "GCS",
    }
    for code, label in vital_labels.items():
        s = temporal.vitals.get(code)
        if s and s.current_value is not None:
            rate_str = f"  ({s.current_rate:+.2f}/hr)" if s.current_rate is not None else ""
            lines.append(f"  {label}: {s.current_value:.1f} {s.unit} [{s.trend}]{rate_str}")

    # Labs
    lab_labels = {
        "2524-7": "Lactate",
        "6690-2": "WBC",
        "718-7":  "Hemoglobin",
        "1988-5": "CRP",
        "2160-0": "Creatinine",
        "33756-8": "Procalcitonin",
    }
    lines.append("")
    lines.append("=== RECENT LABS ===")
    for code, label in lab_labels.items():
        s = temporal.labs.get(code)
        if s and s.current_value is not None:
            rate_str = f"  ({s.current_rate:+.2f}/hr)" if s.current_rate is not None else ""
            lines.append(f"  {label}: {s.current_value:.1f} {s.unit} [{s.trend}]{rate_str}")

    # 72hr trajectory summary
    lines.append("")
    lines.append("=== 72-HOUR TRAJECTORY ===")
    for code, series in {**temporal.vitals, **temporal.labs}.items():
        if series.min_value is not None and series.max_value is not None:
            lines.append(
                f"  {series.display_name}: {series.min_value:.1f}→{series.max_value:.1f} "
                f"{series.unit} (trend: {series.trend})"
            )

    # CEP alerts
    if cep.alerts:
        lines.append("")
        lines.append("=== CEP PATTERN ALERTS ===")
        for alert in cep.alerts:
            lines.append(f"  ⚠ [{alert.severity.upper()}] {alert.pattern_name}: {alert.description}")
            for condition in alert.matched_conditions:
                lines.append(f"    → {condition}")

    # Active conditions
    if patient.conditions:
        lines.append("")
        lines.append("=== ACTIVE CONDITIONS ===")
        for cond in patient.conditions[:10]:
            lines.append(f"  • {cond.display} ({cond.clinical_status})")

    # Medications
    if patient.medications:
        lines.append("")
        lines.append("=== CURRENT MEDICATIONS ===")
        for med in patient.medications[:10]:
            lines.append(f"  • {med.display}")

    lines.append("")
    lines.append("=== NEURAL ANOMALY FLAGS ===")
    if score.neural_flagged_vitals:
        for flag in score.neural_flagged_vitals:
            lines.append(f"  ⚠ {flag}")
    else:
        lines.append("  No anomalous trajectory patterns detected.")

    return "\n".join(lines)


async def _call_agent(
    name: str,
    role: str,
    model_name: str,
    system_prompt: str,
    patient_context: str,
) -> AgentOutput:
    """Call a single Gemini agent asynchronously."""
    try:
        full_prompt = f"{patient_context}\n\nPlease provide your clinical {role} assessment now."

        if _GENAI_NEW:
            client = genai.Client()
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    temperature=0.3,
                ),
            )
            text = response.text.strip()
        else:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_prompt,
            )
            response = await model.generate_content_async(full_prompt)
            text = response.text.strip()

        # Try to extract confidence from response text
        confidence = 0.7
        for line in text.lower().split("\n"):
            if "confidence" in line and "%" in line:
                try:
                    pct_str = [w for w in line.split() if "%" in w][0].replace("%", "")
                    confidence = float(pct_str) / 100
                    break
                except (ValueError, IndexError):
                    pass

        # Extract key findings (lines starting with - or numbers)
        key_findings = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith(("-", "•", "*")) or (len(stripped) > 2 and stripped[0].isdigit() and stripped[1] == "."):
                key_findings.append(stripped.lstrip("-•*0123456789. "))

        return AgentOutput(
            agent_name=name,
            model_used=model_name,
            role=role,
            assessment=text,
            confidence=min(1.0, max(0.0, confidence)),
            key_findings=key_findings[:8],
            recommendations=[],
        )

    except Exception as e:
        log.error("Agent %s failed: %s", name, e)
        return AgentOutput(
            agent_name=name,
            model_used=model_name,
            role=role,
            assessment="",
            confidence=0.0,
            error=str(e),
        )


class AgentFleet:
    """Orchestrates 5 parallel Gemini agents and produces a consensus result."""

    def __init__(self):
        import os
        settings = get_settings()
        if settings.gemini_api_key:
            # New SDK reads GOOGLE_API_KEY env var automatically; set it for consistency
            os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)
            if not _GENAI_NEW:
                genai.configure(api_key=settings.gemini_api_key)
        else:
            log.warning("GEMINI_API_KEY not set — agent fleet will return errors")

    async def run(
        self,
        patient: PatientProfile,
        temporal: TemporalProfile,
        score: AcuityScore,
        cep: CEPResult,
    ) -> ConsensusResult:
        """Run all 5 agents in parallel and build consensus."""
        context = _build_patient_context(patient, temporal, score, cep)

        # Launch all 5 agents concurrently
        outputs = await asyncio.gather(
            _call_agent("triage",      "triage assessment",          _FLASH, TRIAGE_PROMPT,      context),
            _call_agent("depth",       "deep clinical reasoning",    _PRO,   DEPTH_PROMPT,        context),
            _call_agent("literature",  "literature evidence review", _FLASH, LITERATURE_PROMPT,   context),
            _call_agent("treatment",   "treatment planning",         _FLASH, TREATMENT_PROMPT,    context),
            _call_agent("adversarial", "diagnostic challenge",       _FLASH, ADVERSARIAL_PROMPT,  context),
            return_exceptions=False,
        )

        return self._build_consensus(list(outputs))

    def _build_consensus(self, outputs: list[AgentOutput]) -> ConsensusResult:
        """Aggregate 5 agent outputs into a consensus result."""
        successful = [o for o in outputs if not o.error]
        failed = [o for o in outputs if o.error]

        if not successful:
            err_rep = "Gemini API key not valid or missing" if any("API_KEY_INVALID" in str(o.error) for o in failed) else "; ".join(str(o.error or "") for o in failed)
            return ConsensusResult(
                primary_assessment="All agents failed — cannot generate consensus.",
                confidence=0.0,
                agent_agreement_level="no_consensus",
                agent_outputs=outputs,
                dissent=[],
                literature_support="",
                reasoning_trace="Agent fleet failure.",
                sepsis_consensus=False,
                error=err_rep,
            )

        # Extract adversarial dissent
        adversarial = next((o for o in outputs if o.agent_name == "adversarial"), None)
        dissent = []
        if adversarial and adversarial.assessment:
            for line in adversarial.assessment.split("\n"):
                if "ALTERNATIVE DIAGNOSIS" in line.upper() or "alternative" in line.lower():
                    dissent.append(line.strip())

        # Extract structured content from Depth agent
        depth_agent = next((o for o in outputs if o.agent_name == "depth"), None)
        temporal_context = []
        predictions = []
        pathways = []
        
        if depth_agent and depth_agent.assessment:
            current_section = None
            for line in depth_agent.assessment.split("\n"):
                line = line.strip()
                if not line: continue
                
                if line.startswith("TEMPORAL CONTEXT:"):
                    current_section = "temporal"
                    continue
                elif line.startswith("PREDICTIONS:"):
                    current_section = "predictions"
                    continue
                elif line.startswith("CLINICAL PATHWAYS:"):
                    current_section = "pathways"
                    continue
                elif line.startswith("CLINICAL IMPRESSION:"):
                    current_section = "impression"
                    continue
                elif line.endswith(":"):
                    current_section = None
                    
                if current_section == "temporal" and line.startswith(("-", "•", "*")):
                    temporal_context.append(line.lstrip("-•* "))
                elif current_section == "predictions" and line.startswith(("-", "•", "*")):
                    predictions.append(line.lstrip("-•* "))
                elif current_section == "pathways" and line.startswith(("-", "•", "*")):
                    pathways.append(line.lstrip("-•* "))

        # Extract treatment recommendations from treatment agent
        treatment_agent = next((o for o in outputs if o.agent_name == "treatment"), None)
        if treatment_agent and treatment_agent.assessment:
            for line in treatment_agent.assessment.split("\n"):
                stripped = line.strip()
                if stripped.startswith(("-", "•")) or (len(stripped) > 3 and stripped[0].isdigit()):
                    pathways.append(stripped.lstrip("-•0123456789. "))

        # Literature support
        lit_agent = next((o for o in outputs if o.agent_name == "literature"), None)
        literature_support = lit_agent.assessment if lit_agent else ""

        # Confidence: based on agreement of non-adversarial agents
        non_adv = [o for o in successful if o.agent_name != "adversarial"]
        avg_confidence = sum(o.confidence for o in non_adv) / len(non_adv) if non_adv else 0.5

        # Penalize if adversarial dissent is strong (long alternative list)
        if adversarial and len(dissent) >= 3:
            avg_confidence *= 0.8
        elif adversarial and len(dissent) >= 2:
            avg_confidence *= 0.9

        avg_confidence = round(min(0.95, avg_confidence), 3)

        # Agreement level
        if avg_confidence >= 0.85:
            agreement = "unanimous"
        elif avg_confidence >= 0.65:
            agreement = "majority"
        elif avg_confidence >= 0.45:
            agreement = "split"
        else:
            agreement = "no_consensus"

        # Build reasoning trace
        depth_agent = next((o for o in outputs if o.agent_name == "depth"), None)
        triage_agent = next((o for o in outputs if o.agent_name == "triage"), None)

        primary = (depth_agent or triage_agent or successful[0]).assessment
        primary_short = primary[:500] + "..." if len(primary) > 500 else primary

        trace = f"""=== GEMINI AGENT FLEET CONSENSUS ===
Agreement: {agreement.upper()} | Confidence: {avg_confidence:.0%}
Agents run: {len(successful)}/5 | Failed: {len(failed)}/5

--- TRIAGE (Flash) ---
{triage_agent.assessment if triage_agent and not triage_agent.error else '[failed]'}

--- DEPTH REASONING (Pro) ---
{depth_agent.assessment if depth_agent and not depth_agent.error else '[failed]'}

--- ADVERSARIAL CHALLENGE (Pro) ---
{adversarial.assessment if adversarial and not adversarial.error else '[failed]'}

--- TREATMENT (Pro) ---
{treatment_agent.assessment if treatment_agent and not treatment_agent.error else '[failed]'}
"""

        # Sepsis consensus — check if most agents mention sepsis
        sepsis_keywords = {"sepsis", "septic", "sirs", "infection"}
        sepsis_votes = sum(
            1 for o in non_adv
            if any(kw in o.assessment.lower() for kw in sepsis_keywords)
        )
        sepsis_consensus = sepsis_votes >= 2

        return ConsensusResult(
            primary_assessment=primary_short,
            confidence=avg_confidence,
            agent_agreement_level=agreement,
            agent_outputs=outputs,
            temporal_context=temporal_context,
            predictions=predictions,
            clinical_pathways=pathways[:10],
            dissent=dissent[:5],
            literature_support=literature_support[:1000] if literature_support else "",
            reasoning_trace=trace,
            sepsis_consensus=sepsis_consensus,
        )
