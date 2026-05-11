"""
Layer 7 — Privacy: TenSEAL Homomorphic Encryption wrapper.

Encrypts vital sign vectors so scoring can be performed without
accessing raw patient values. Uses CKKS scheme (approximate arithmetic
for real numbers) from Microsoft SEAL via TenSEAL.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# VITAL_CODES used as the encrypted vector feature order
VITAL_CODES = [
    "8867-4",   # HR
    "8480-6",   # SBP
    "8310-5",   # Temp
    "9279-1",   # RR
    "59408-5",  # SpO2
]


class HomomorphicEncryptor:
    """
    Wraps TenSEAL CKKS encryption for vital sign vectors.
    Falls back to plaintext mode if TenSEAL is not installed.
    """

    def __init__(self):
        self._context = None
        self._available = False
        try:
            import tenseal as ts
            self._ts = ts
            self._context = ts.context(
                ts.SCHEME_TYPE.CKKS,
                poly_modulus_degree=8192,
                coeff_mod_bit_sizes=[60, 40, 40, 60],
            )
            self._context.global_scale = 2**40
            self._context.generate_galois_keys()
            self._available = True
            log.info("TenSEAL CKKS context initialized — HE active")
        except ImportError:
            log.warning("TenSEAL not installed — HE disabled, using plaintext mode")
        except Exception as e:
            log.warning("TenSEAL init failed (%s) — HE disabled", e)

    @property
    def is_available(self) -> bool:
        return self._available

    def encrypt_vitals(self, vitals_dict: dict[str, float]) -> dict:
        """
        Encrypt a dict of {loinc_code: value} into a CKKS vector.

        Returns a dict describing the encrypted state:
          - If HE available: {"encrypted": True, "vector": <CKKSVector>, "codes": [...]}
          - If HE unavailable: {"encrypted": False, "values": {...}}
        """
        vector_values = [vitals_dict.get(code, 0.0) for code in VITAL_CODES]

        if self._available:
            enc_vec = self._ts.ckks_vector(self._context, vector_values)
            return {
                "encrypted": True,
                "vector": enc_vec,
                "codes": VITAL_CODES,
                "context": self._context,
            }

        return {
            "encrypted": False,
            "values": {code: vitals_dict.get(code, 0.0) for code in VITAL_CODES},
        }

    def compute_deviation_from_thresholds(
        self, encrypted: dict, thresholds: list[float]
    ) -> dict:
        """
        Compute (vitals - thresholds) on encrypted data.
        Returns dict with deviation result.
        """
        if encrypted.get("encrypted") and self._available:
            enc_vec = encrypted["vector"]
            enc_thresh = self._ts.ckks_vector(self._context, thresholds)
            deviation = enc_vec - enc_thresh
            result_plain = deviation.decrypt()
            return {
                "computed_on": "ciphertext",
                "deviations": {
                    code: round(dev, 3)
                    for code, dev in zip(VITAL_CODES, result_plain)
                },
            }

        # Plaintext fallback
        plain_values = encrypted.get("values", {})
        return {
            "computed_on": "plaintext (HE unavailable)",
            "deviations": {
                code: round(plain_values.get(code, 0.0) - thresh, 3)
                for code, thresh in zip(VITAL_CODES, thresholds)
            },
        }

    def decrypt_result(self, encrypted: dict) -> list[float]:
        """Decrypt a CKKS vector back to plaintext."""
        if encrypted.get("encrypted") and self._available:
            return [round(v, 3) for v in encrypted["vector"].decrypt()]
        return [encrypted.get("values", {}).get(c, 0.0) for c in VITAL_CODES]


# Singleton
_encryptor: HomomorphicEncryptor | None = None


def get_encryptor() -> HomomorphicEncryptor:
    global _encryptor
    if _encryptor is None:
        _encryptor = HomomorphicEncryptor()
    return _encryptor
