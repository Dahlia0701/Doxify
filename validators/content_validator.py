"""deterministic checks for structured factual claims.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

from validators.common import ValidationResult


def validate_metric_claims(claims: Sequence[dict], trusted_metrics: Mapping[str, float]) -> ValidationResult:
    """Check structured claims: {metric_id, value, text} against trusted metrics.

    Text is required but is not semantically verified by this function.
    """
    result = ValidationResult()
    for i, claim in enumerate(claims):
        key = claim.get("metric_id")
        text = claim.get("text")
        value = claim.get("value")
        if not isinstance(text, str) or not text.strip():
            result.add("critical", "empty_claim", f"Claim {i + 1} has no explanatory text.")
        if not isinstance(key, str) or key not in trusted_metrics:
            result.add("warning", "unknown_metric", f"Claim {i + 1} has no known reference metric; it was not verified.")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            result.add("critical", "invalid_claim_value", f"Claim {i + 1} has no finite numeric value.")
            continue
        expected = trusted_metrics[key]
        if not math.isclose(float(value), float(expected), rel_tol=1e-9, abs_tol=0.01):
            result.add("critical", "incorrect_claim", f"Claim {i + 1} disagrees with trusted metric '{key}'.")
        # IMPORTANT: this does not prove the accompanying sentence matches the numeric value.
        result.add("warning", "prose_unverified", f"Claim {i + 1} text still needs separate semantic review.")
    return result
