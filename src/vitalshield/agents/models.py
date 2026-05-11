"""Layer 6 — Agent Fleet: Agent output models."""

from __future__ import annotations
from pydantic import BaseModel, Field


class AgentOutput(BaseModel):
    agent_name: str
    model_used: str
    role: str
    assessment: str                  # main clinical assessment text
    confidence: float                # 0-1 agent self-reported confidence
    key_findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    error: str | None = None         # if agent failed


class ConsensusResult(BaseModel):
    """Aggregated result from the 5-agent fleet."""
    primary_assessment: str
    confidence: float                     # 0-1 from inter-agent agreement
    agent_agreement_level: str            # unanimous / majority / split / no_consensus
    
    temporal_context: list[str] = Field(default_factory=list)
    predictions: list[str] = Field(default_factory=list)
    clinical_pathways: list[str] = Field(default_factory=list)
    
    agent_outputs: list[AgentOutput]
    dissent: list[str]                    # adversarial alternatives
    literature_support: str
    reasoning_trace: str                  # full reasoning chain
    sepsis_consensus: bool
    error: str | None = None
