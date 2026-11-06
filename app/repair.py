from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.intent import Intent
from app.schemas import AnyDoc, ChartSpec, DeckDoc, PdfDoc, WorkbookDoc
from validators.common import Issue

RETRY_CODES = {"file_missing", "file_empty", "cannot_open"}  


@dataclass
class RepairOutcome:
    doc: AnyDoc
    actions: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.actions)


def shorten(text: str, limit: int) -> str:
   
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[: limit - 1]) + "…"


def _repair_deck(doc: DeckDoc, issues: list[Issue], attempt: int, actions: list[str]) -> DeckDoc:
    codes = {i.code for i in issues}
    data = doc.model_dump()
    if "slide_count_requested" in codes:
        want = next(i.data.get("want") for i in issues if i.code == "slide_count_requested")
        have = len(data["slides"])
        if want is not None and have > want:
            data["slides"] = data["slides"][:want]
            actions.append(f"Removed {have - want} trailing slide(s) to match the requested {want}.")
    if codes & {"text_overflow", "title_overflow"}:
        limit = {1: 150, 2: 100}.get(attempt, 80)
        touched = set()
        for n in sorted({i.data.get("slide") for i in issues if i.code in ("text_overflow", "title_overflow")} - {None}):
            if not 1 <= n <= len(data["slides"]):
                continue
            s = data["slides"][n - 1]
            before = repr(s)
            s["title"] = shorten(s["title"], 70)
            for key, cap in (("bullets", 6), ("left", 5), ("right", 5)):
                s[key] = [shorten(b, limit) for b in s[key][:cap]]
            if repr(s) != before:
                touched.add(n)
        if touched:
            actions.append(f"Shortened text on slide(s) {', '.join(map(str, sorted(touched)))} to fit (limit {limit} characters per bullet).")
    return DeckDoc.model_validate(data) if actions else doc


def _default_chart(doc: WorkbookDoc) -> ChartSpec | None:
    for sheet in doc.sheets:
        if len(sheet.rows) < 2:
            continue
        cols = sheet.all_columns
        cat = next((c for c in cols if not sheet.is_numeric(c)), None)
        vals = [c for c in cols if sheet.is_numeric(c) and c != cat][:2]
        if cat and vals:
            return ChartSpec(kind="bar", title=f"{', '.join(vals)} by {cat}"[:100], sheet=sheet.name,
                             category_column=cat, value_columns=vals)
    return None


def _repair_workbook(doc: WorkbookDoc, issues: list[Issue], actions: list[str]) -> WorkbookDoc:
    codes = {i.code for i in issues}
    data = doc.model_dump()
    if "dashboard_missing" in codes and not data["include_dashboard"]:
        data["include_dashboard"] = True
        actions.append("Added a Dashboard sheet because one was requested.")
    if "charts_missing" in codes and not data["charts"]:
        chart = _default_chart(doc)
        if chart:
            data["charts"] = [chart.model_dump()]
            actions.append(f"Added a default chart ('{chart.title}') because charts were requested.")
    if "formulas_missing" in codes:
        n = 0
        for k, s in enumerate(data["sheets"]):
            tmp = doc.sheets[k]
            if s["rows"] and any(tmp.is_numeric(c) for c in tmp.columns) and not s["totals_row"]:
                s["totals_row"] = True
                n += 1
        if n:
            actions.append(f"Added SUM totals rows to {n} sheet(s) because formulas were requested.")
    return WorkbookDoc.model_validate(data) if actions else doc


def _repair_pdf(doc: PdfDoc, issues: list[Issue], actions: list[str]) -> PdfDoc:
    data = doc.model_dump()
    for i in issues:
        if i.code == "target_pages":
            data["target_pages"] = i.data["target"]
            actions.append(f"Recorded the {i.data['target']}-page target.")
        elif i.code == "page_count" and "pages" in i.data:
            have, want = i.data["pages"], i.data["target"]
            if have > want:
                if data["section_breaks"]:
                    data["section_breaks"] = False
                    actions.append(f"Removed page breaks between sections to shorten from {have} to {want} pages.")
                elif data["font_scale"] > 0.8:
                    data["font_scale"] = round(max(0.8, data["font_scale"] - 0.1), 2)
                    actions.append(f"Reduced the text size to {data['font_scale']:.0%} to shorten from {have} to {want} pages.")
            elif have < want:
                if not data["section_breaks"]:
                    data["section_breaks"] = True
                    actions.append(f"Started each section on a new page to lengthen from {have} to {want} pages.")
                elif data["font_scale"] < 1.2:
                    data["font_scale"] = round(min(1.2, data["font_scale"] + 0.1), 2)
                    actions.append(f"Increased the text size to {data['font_scale']:.0%} to lengthen from {have} to {want} pages.")
    return PdfDoc.model_validate(data) if actions else doc


def repair(doc: AnyDoc, issues: list[Issue], intent: Intent, attempt: int = 1) -> RepairOutcome:
    """Apply every safe repair that matches the issues. Returns the (possibly new) plan and the actions taken."""
    actions: list[str] = []
    try:
        if isinstance(doc, DeckDoc):
            new = _repair_deck(doc, issues, attempt, actions)
        elif isinstance(doc, WorkbookDoc):
            new = _repair_workbook(doc, issues, actions)
        else:
            new = _repair_pdf(doc, issues, actions)
    except Exception:  
        return RepairOutcome(doc, [])
    if not actions and any(i.code in RETRY_CODES for i in issues):
        actions.append("The output file was unreadable, so it was generated again.")
    return RepairOutcome(new, actions)
