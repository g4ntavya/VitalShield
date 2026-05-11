# VitalShield

VitalShield is a privacy-first clinical intelligence dashboard designed to transform messy hospital data into actionable clinical trajectories. It operates as a Model Context Protocol (MCP) server, providing a high-fidelity terminal interface that prioritizes speed, security, and adversarial reasoning.

## Core Philosophy

Traditional hospital software shows snapshots. VitalShield calculates velocity. By shifting the focus from current values to temporal drift, we help clinicians spot deterioration hours before it becomes critical.

## Key Features

### Temporal Trajectory Engine
The heart of VitalShield is a deterministic engine that calculates the first derivative of patient health. It re-indexes messy FHIR records, standardizes timezones, and computes the rate of change for critical vitals and lab values over a 72-hour sliding window.

### Bloomberg-Style Terminal UI
A high-density command-line interface built for peak performance. It features real-time ASCII sparklines for heart rate, blood pressure, oxygen saturation, respiratory rate, and temperature. The UI uses an asynchronous dual-layer rendering system that displays raw clinical reality instantly while deep AI reasoning processes in the background.

### Complex Event Processing (CEP)
The system includes a rule-based engine that identifies clinical patterns such as Early Sepsis, Septic Shock, Renal Decline, and Hemodynamic Failure. These patterns are based on established clinical guidelines including NEWS2 and qSOFA, ensuring a reliable safety net that functions independently of any AI models.

### Adversarial Agent Fleet
When higher-level synthesis is required, VitalShield deploys a fleet of parallel Gemini agents. This fleet consists of specialized roles including Triage, Depth Reasoning, Literature Review, and a dedicated Adversarial agent whose sole job is to challenge the primary diagnosis and prevent anchor bias.

### Privacy-Native Architecture
VitalShield implements a multi-layered privacy firewall:
- Local Processing: All sensitive patient identifiers are redacted locally before data reaches any external inference engine.
- Zero-Knowledge Proofs: Access control is managed via ZK circuits, proving clinician authorization without exposing identity.
- Homomorphic Encryption: Sensitive numerical vitals can be processed via TenSEAL, allowing mathematical operations on encrypted data.

### FHIR R4 Integration
Native support for HL7 FHIR R4 standards. VitalShield can ingest data from any compliant EMR system, extracting demographics, observations, conditions, and encounters to build a complete clinical profile.

## Installation

Ensure you have Python 3.10 or higher installed.

1. Clone the repository and navigate to the project directory.
2. Create a virtual environment and activate it.
3. Install dependencies using the provided requirements file or pyproject.toml.
4. Configure your environment variables in a .env file, including your Gemini API key.

## Usage

To launch the clinical dashboard for a specific patient:

```bash
PYTHONPATH=src python scripts/demo.py --patient-id [YOUR_PATIENT_ID]
```

To run the MCP server for integration with AI agents:

```bash
python src/vitalshield/server.py
```

## Disclaimer

VitalShield is a clinical decision support tool designed for educational and research purposes. It is not a replacement for professional medical judgment. All clinical decisions must be made by a qualified healthcare professional.
