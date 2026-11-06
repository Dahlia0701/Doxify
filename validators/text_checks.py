"""basic checks for text quality and completeness"""
from __future__ import annotations

import re
from typing import Iterator

from app.schemas import AnyDoc, BulletsBlock, DeckDoc, ParagraphBlock, PdfDoc, TableBlock, WorkbookDoc
from validators.common import ValidationResult

_PLACEHOLDER = re.compile(
    r"lorem ipsum|\bTBD\b|\bTODO\b|\bXXX+\b|\[(?:insert|your|add|company|name|date)[^\]]*\]|<\s*insert[^>]*>|\bplaceholder\b",
    re.I,
)


def iter_text(doc: AnyDoc) -> Iterator[tuple[str, str]]:
    """Yield (location, text) for every human-readable string in the plan."""
    yield "title", doc.title
    if isinstance(doc, DeckDoc):
        for i, s in enumerate(doc.slides, 1):
            where = f"slide {i}"
            for t in [s.title, s.subtitle, s.left_title, s.right_title, *s.bullets, *s.left, *s.right]:
                if t:
                    yield where, t
            for k in s.kpis:
                yield where, f"{k.label} {k.value} {k.note}"
            if s.table:
                for c in s.table.columns:
                    yield where, str(c)
                for r in s.table.rows:
                    for v in r:
                        if isinstance(v, str):
                            yield where, v
    elif isinstance(doc, PdfDoc):
        for sec in doc.sections:
            yield f"section '{sec.heading}'", sec.heading
            for b in sec.blocks:
                if isinstance(b, ParagraphBlock):
                    yield f"section '{sec.heading}'", b.text
                elif isinstance(b, BulletsBlock):
                    for it in b.items:
                        yield f"section '{sec.heading}'", it
                elif isinstance(b, TableBlock):
                    for r in b.table.rows:
                        for v in r:
                            if isinstance(v, str):
                                yield f"section '{sec.heading}'", v
    elif isinstance(doc, WorkbookDoc):
        for sh in doc.sheets:
            for c in sh.columns:
                yield f"sheet '{sh.name}'", c


def check_plan_text(doc: AnyDoc, result: ValidationResult) -> None:
    seen: set[str] = set()
    for where, text in iter_text(doc):
        m = _PLACEHOLDER.search(text)
        if m and where not in seen:
            seen.add(where)
            result.add("warning", "placeholder_text", f"Possible placeholder text ('{m.group(0)}') in {where}.")
