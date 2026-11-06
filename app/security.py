"""Security helpers: formula allow list and spreadsheet formula-injection protection."""
from __future__ import annotations

import math

from app.schemas import FORMULA_ARITY, is_number_text

DANGEROUS_PREFIXES = ("=", "+", "-", "@")
CONTROL_PREFIXES = ("\t", "\r", "\n")


class SecurityError(ValueError):
    pass


def escape_cell(value):
    """Neutralise untrusted cell data.

    - Real numbers stay numbers (so "-5" becomes -5, not text).
    - Text that could be read as a formula by Excel gets a leading apostrophe.
    """
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped and is_number_text(stripped):
        number = float(stripped) if any(c in stripped for c in ".eE") else int(stripped)
        if not (isinstance(number, float) and not math.isfinite(number)):
            return number
    if value.startswith(DANGEROUS_PREFIXES + CONTROL_PREFIXES) or value.lstrip().startswith(DANGEROUS_PREFIXES):
        return "'" + value
    return value


def quote_sheet(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def build_formula(formula_type: str, refs: list[str]) -> str:
    """Build an Excel formula from an allowlisted type and cell references.

    `refs` are cell addresses produced by our own code (like "B2"), never LLM text.
    """
    if formula_type not in FORMULA_ARITY:
        raise SecurityError(f"formula type '{formula_type}' is not allowed")
    lo, hi = FORMULA_ARITY[formula_type]
    if not lo <= len(refs) <= hi:
        raise SecurityError(f"formula '{formula_type}' got {len(refs)} inputs, needs {lo}-{hi}")
    if formula_type == "sum":
        return f"=SUM({','.join(refs)})"
    if formula_type == "average":
        return f"=AVERAGE({','.join(refs)})"
    a, b = refs
    if formula_type == "difference":
        return f"={a}-{b}"
    if formula_type == "ratio":
        return f'=IF({b}=0,"",{a}/{b})'
   
    return f'=IF({a}=0,"",({b}-{a})/{a})'
