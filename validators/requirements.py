"""Validates that the generated file meets the requirements of the plan.
"""
from __future__ import annotations

import re

from app.intent import Intent
from app.schemas import AnyDoc, DeckDoc, PdfDoc, WorkbookDoc
from validators.common import ValidationResult


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower())) - {"and", "the", "a", "an", "of"}


def section_covered(requested: str, headings: list[str]) -> bool:
    want = _words(requested)
    return bool(want) and any(want <= _words(h) for h in headings)


def check_requirements(doc: AnyDoc, intent: Intent) -> ValidationResult:
    r = ValidationResult()
    if isinstance(doc, DeckDoc):
        if intent.slide_count_explicit and intent.slide_count is not None and len(doc.slides) != intent.slide_count:
            r.add("critical", "slide_count_requested",
                  f"{intent.slide_count} slides were requested but the plan has {len(doc.slides)}.",
                  {"have": len(doc.slides), "want": intent.slide_count})
    elif isinstance(doc, PdfDoc):
        headings = [s.heading for s in doc.sections]
        for name in intent.sections:
            if not section_covered(name, headings):
                r.add("critical", "section_not_planned", f"The requested section '{name}' is not in the plan.", {"section": name})
        if intent.page_count is not None and doc.target_pages != intent.page_count:
            r.add("warning", "target_pages", "The page target was not recorded in the plan.", {"target": intent.page_count})
    elif isinstance(doc, WorkbookDoc):
        if intent.wants_dashboard and not doc.include_dashboard:
            r.add("critical", "dashboard_missing", "A dashboard was requested but the plan does not include one.")
        if intent.wants_charts and not doc.charts:
            r.add("warning", "charts_missing", "Charts were requested but the plan has none.")
        if intent.wants_formulas and not any(s.computed_columns or s.totals_row for s in doc.sheets):
            r.add("warning", "formulas_missing", "Formulas were requested but the plan has no computed columns or totals.")
    return r
