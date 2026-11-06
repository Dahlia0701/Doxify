"""Turns raw LLM text into a validated document plan (the intermediate representation).

The LLM only ever produces JSON text. This module parses it with Pydantic; nothing the
model writes is executed or evaluated.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from app.schemas import DOC_MODELS, AnyDoc


class PlanError(Exception):
    """The LLM output could not be turned into a valid plan. Message is user-facing."""


def _extract_json_text(raw: str) -> str:
    text = raw.strip()
    # models often wrap JSON in ```json fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    # or add a sentence before/after the object
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return text


def parse_plan(raw: str, fmt: str = "xlsx") -> AnyDoc:
    """Parse and validate model output for the given format ('pdf' | 'pptx' | 'xlsx')."""
    model = DOC_MODELS.get(fmt)
    if model is None:
        raise PlanError(f"Unsupported output format '{fmt}'. Use pdf, pptx or xlsx.")
    try:
        data = json.loads(_extract_json_text(raw))
    except json.JSONDecodeError as exc:
        raise PlanError(f"The model did not return valid JSON ({exc.msg}). Please retry.") from exc
    if not isinstance(data, dict):
        raise PlanError("The model returned JSON, but not an object. Please retry.")
    declared = data.get("type", fmt)
    if declared != fmt:
        raise PlanError(f"The model produced a '{declared}' plan but '{fmt}' was requested.")
    data["type"] = fmt
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "document"
        raise PlanError(f"The generated plan is invalid at '{where}': {first['msg']}") from exc
