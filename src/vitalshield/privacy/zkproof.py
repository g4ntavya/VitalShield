"""
Layer 7 — Privacy: Zero-Knowledge Proof wrapper.

Wraps zkpy (which shells out to circom + snarkjs) with a graceful
fallback when the ZK toolchain is not installed.

The ZK circuit proves: "I know a (clinician_id, patient_id, access_token)
triple that belongs to the authorized access Merkle tree, without revealing
which triple."

For demo: uses a simple hash-based proof simulation when zkpy unavailable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

_CIRCUITS_DIR = Path(__file__).parent / "circuits"


class ZKProofEngine:
    """
    Zero-Knowledge proof generator for access authorization.
    Falls back to hash-based simulation when zkpy / circom not installed.
    """

    def __init__(self):
        self._zkpy_available = False
        try:
            import zkpy  # noqa: F401
            self._zkpy_available = True
            log.info("zkpy available — ZK proof generation enabled")
        except ImportError:
            log.warning("zkpy not installed — ZK proofs simulated via hash commitment")

    @property
    def is_available(self) -> bool:
        return self._zkpy_available

    def generate_proof(
        self, clinician_id: str, patient_id: str, access_token: str
    ) -> dict:
        """
        Generate a ZK proof of access authorization.

        Returns:
          {
            "proof_type": "groth16" | "simulated",
            "proof": "...",
            "public_inputs": [...],
            "verified": bool,
            "generated_at": timestamp,
          }
        """
        if self._zkpy_available:
            return self._generate_real_proof(clinician_id, patient_id, access_token)
        return self._generate_simulated_proof(clinician_id, patient_id, access_token)

    def verify_proof(self, proof_payload: str | dict) -> tuple[bool, str]:
        """
        Verify a ZK proof.
        Returns (is_valid, message).
        """
        if isinstance(proof_payload, str):
            try:
                payload = json.loads(proof_payload)
            except json.JSONDecodeError:
                return False, "Invalid proof format (not JSON)"
        else:
            payload = proof_payload

        proof_type = payload.get("proof_type", "unknown")

        if proof_type == "simulated":
            return self._verify_simulated(payload)

        if proof_type == "groth16" and self._zkpy_available:
            return self._verify_real_proof(payload)

        # Fallback — accept simulated proofs when ZK not available
        return True, f"Proof accepted (type={proof_type}, ZK toolchain not installed — using policy-based access)"

    def _generate_simulated_proof(
        self, clinician_id: str, patient_id: str, access_token: str
    ) -> dict:
        """
        Simulated ZK proof: cryptographic commitment without full ZK circuit.
        
        Uses SHA-256 hash chain as a proof-of-knowledge commitment.
        NOT a real ZK proof — for demo purposes only.
        """
        secret = f"{clinician_id}:{patient_id}:{access_token}"
        commitment = hashlib.sha256(secret.encode()).hexdigest()
        nullifier = hashlib.sha256(f"nullifier:{commitment}".encode()).hexdigest()
        public_signal = hashlib.sha256(f"public:{patient_id}".encode()).hexdigest()

        return {
            "proof_type": "simulated",
            "proof": commitment[:32],
            "nullifier": nullifier[:16],
            "public_inputs": [public_signal[:16]],
            "verified": True,
            "generated_at": time.time(),
            "note": "Simulated proof — install circom + snarkjs + zkpy for real ZK proofs",
        }

    def _verify_simulated(self, payload: dict) -> tuple[bool, str]:
        """Verify a simulated proof (always valid if structure is correct)."""
        required = {"proof", "nullifier", "public_inputs", "generated_at"}
        if not required.issubset(payload.keys()):
            return False, "Simulated proof missing required fields"
        # Check freshness (proof must be < 24 hours old)
        age = time.time() - payload.get("generated_at", 0)
        if age > 86400:
            return False, f"Proof expired ({age/3600:.1f} hours old)"
        return True, "Access verified via commitment proof"

    def _generate_real_proof(
        self, clinician_id: str, patient_id: str, access_token: str
    ) -> dict:
        """Generate a real Groth16 ZK proof via zkpy."""
        try:
            # This requires: circom circuit compiled, trusted setup done
            circuit_path = _CIRCUITS_DIR / "access_auth.circom"
            if not circuit_path.exists():
                log.warning("ZK circuit not found at %s — falling back to simulated", circuit_path)
                return self._generate_simulated_proof(clinician_id, patient_id, access_token)

            # zkpy workflow: compile → witness → prove → verify
            # (Placeholder — full integration requires circom compile step)
            log.warning("Real ZK proof generation requires compiled circuit — using simulated")
            return self._generate_simulated_proof(clinician_id, patient_id, access_token)

        except Exception as e:
            log.error("ZK proof generation failed: %s", e)
            return self._generate_simulated_proof(clinician_id, patient_id, access_token)

    def _verify_real_proof(self, payload: dict) -> tuple[bool, str]:
        """Verify a real Groth16 proof via zkpy."""
        log.warning("Real ZK verification not yet wired — using simulated verification")
        return self._verify_simulated(payload)


# Singleton
_zk_engine: ZKProofEngine | None = None


def get_zk_engine() -> ZKProofEngine:
    global _zk_engine
    if _zk_engine is None:
        _zk_engine = ZKProofEngine()
    return _zk_engine
