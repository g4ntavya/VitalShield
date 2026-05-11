"""
Layer 2 — SNOMED CT + LOINC Terminology Normalization.

Lookup order:
  1. Check local lookup table (JSON)        — fast, covers 95% ICU/ED codes
  2. Fuzzy match against known display names — catches free-text variations
  3. Pass through (unknown, low confidence)  — never crashes the pipeline
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from rapidfuzz import process, fuzz

log = logging.getLogger(__name__)

_LOOKUPS_DIR = Path(__file__).parent / "lookups"


class TerminologyMapper:
    """
    Unified SNOMED CT + LOINC terminology mapper.

    Ships with embedded lookup tables covering the ~200 most common
    ICU/ED LOINC observation codes and ~300 SNOMED CT diagnosis codes.
    """

    def __init__(self):
        self._loinc: dict[str, dict] = {}        # code/alias → canonical entry
        self._snomed: dict[str, dict] = {}       # code/alias → canonical entry
        self._loinc_display_index: dict[str, str] = {}   # lower-display → loinc_code
        self._snomed_display_index: dict[str, str] = {}  # lower-display → snomed_code
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        loinc_path = _LOOKUPS_DIR / "loinc_common.json"
        snomed_path = _LOOKUPS_DIR / "snomed_common.json"

        if loinc_path.exists():
            with open(loinc_path) as f:
                data = json.load(f)
            for entry in data:
                canonical = entry["code"]
                self._loinc[canonical] = entry
                for alias in entry.get("aliases", []):
                    self._loinc[alias.lower()] = entry
                self._loinc_display_index[entry["display"].lower()] = canonical
        else:
            log.warning("LOINC lookup table not found at %s", loinc_path)

        if snomed_path.exists():
            with open(snomed_path) as f:
                data = json.load(f)
            for entry in data:
                canonical = entry["code"]
                self._snomed[canonical] = entry
                for alias in entry.get("aliases", []):
                    self._snomed[alias.lower()] = entry
                self._snomed_display_index[entry["display"].lower()] = canonical
        else:
            log.warning("SNOMED CT lookup table not found at %s", snomed_path)

        self._loaded = True

    # ── LOINC ─────────────────────────────────────────────────────────────────

    def normalize_loinc(
        self, code: str, display: str = "", system: str = ""
    ) -> tuple[str, str, float]:
        """
        Return (canonical_loinc_code, canonical_display, confidence).
        confidence: 1.0 = exact, 0.8 = alias, 0.6 = fuzzy, 0.3 = unknown
        """
        self._ensure_loaded()

        # 1. Already canonical (direct lookup)
        if code in self._loinc:
            entry = self._loinc[code]
            return entry["code"], entry["display"], 1.0

        # 2. Alias lookup
        key = code.lower()
        if key in self._loinc:
            entry = self._loinc[key]
            return entry["code"], entry["display"], 0.85

        # 3. Display exact match
        display_key = display.lower().strip()
        if display_key in self._loinc_display_index:
            canonical = self._loinc_display_index[display_key]
            entry = self._loinc[canonical]
            return entry["code"], entry["display"], 0.9

        # 4. Fuzzy match on display
        if display and self._loinc_display_index:
            choices = list(self._loinc_display_index.keys())
            match = process.extractOne(display_key, choices, scorer=fuzz.token_sort_ratio)
            if match and match[1] >= 70:
                canonical = self._loinc_display_index[match[0]]
                entry = self._loinc[canonical]
                score = match[1] / 100 * 0.7
                return entry["code"], entry["display"], score

        # 5. Unknown — pass through
        log.debug("LOINC code not normalizable: code=%s display=%s", code, display)
        return code, display or code, 0.3

    def normalize_snomed(
        self, code: str, display: str = "", system: str = ""
    ) -> tuple[str, str, float]:
        """Return (canonical_snomed_code, canonical_display, confidence)."""
        self._ensure_loaded()

        if code in self._snomed:
            entry = self._snomed[code]
            return entry["code"], entry["display"], 1.0

        key = code.lower()
        if key in self._snomed:
            entry = self._snomed[key]
            return entry["code"], entry["display"], 0.85

        display_key = display.lower().strip()
        if display_key in self._snomed_display_index:
            canonical = self._snomed_display_index[display_key]
            entry = self._snomed[canonical]
            return entry["code"], entry["display"], 0.9

        if display and self._snomed_display_index:
            choices = list(self._snomed_display_index.keys())
            match = process.extractOne(display_key, choices, scorer=fuzz.token_sort_ratio)
            if match and match[1] >= 65:
                canonical = self._snomed_display_index[match[0]]
                entry = self._snomed[canonical]
                score = match[1] / 100 * 0.7
                return entry["code"], entry["display"], score

        return code, display or code, 0.3

    # ── Unit Normalization ────────────────────────────────────────────────────

    _UNIT_MAP: dict[str, str] = {
        # Temperature
        "f": "Cel", "[degF]": "Cel",
        "celsius": "Cel", "°c": "Cel", "c": "Cel",
        # Pressure
        "mmhg": "mm[Hg]", "mm hg": "mm[Hg]", "mmHg": "mm[Hg]",
        # Rate
        "/min": "/min", "bpm": "/min", "breaths/min": "/min",
        # Percent
        "%": "%", "percent": "%",
        # Volume
        "ml": "mL", "l": "L",
        # Concentration
        "mg/dl": "mg/dL", "g/dl": "g/dL",
        "mmol/l": "mmol/L", "umol/l": "umol/L",
        # Counts
        "10*3/ul": "10*3/uL", "k/ul": "10*3/uL",
    }

    def normalize_unit(self, unit: str, loinc_code: str = "") -> str:
        """Return standardized unit string."""
        mapped = self._UNIT_MAP.get(unit.lower().strip(), unit)
        return mapped

    def convert_temperature(self, value: float, from_unit: str) -> float:
        """Convert temperature to Celsius if needed."""
        from_unit_lower = from_unit.lower().strip()
        if from_unit_lower in ("f", "[degf]", "fahrenheit"):
            return round((value - 32) * 5 / 9, 2)
        return value


# Singleton
_mapper: TerminologyMapper | None = None


def get_mapper() -> TerminologyMapper:
    global _mapper
    if _mapper is None:
        _mapper = TerminologyMapper()
    return _mapper
