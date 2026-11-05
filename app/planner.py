"""Turns raw LLM text into a validated workbook plan. (LLM call + prompt come Friday.)"""
from __future__ import annotations

import json

from pydantic import ValidationError

from app.schemas import WorkbookDoc


class PlanError(Exception):
    """The LLM output could not be turned into a valid plan. Message is user-facing."""


def parse_plan(raw: str) -> WorkbookDoc:
    text = raw.strip()
    # models often wrap JSON in ```json fences
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError(f"The model did not return valid JSON ({exc.msg}). Please retry.") from exc
    if not isinstance(data, dict):
        raise PlanError("The model returned JSON, but not an object. Please retry.")
    try:
        return WorkbookDoc.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "document"
        raise PlanError(f"The generated plan is invalid at '{where}': {first['msg']}") from exc
