"""Re-opens the generated PDF with pypdf and checks pages and required content
"""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from app.schemas import ChartBlock, PdfDoc, TableBlock
from validators.common import ValidationResult, check_file_basic


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _count_images(page) -> int:
    try:
        xobj = page["/Resources"].get_object().get("/XObject")
        if xobj is None:
            return 0
        return sum(1 for v in xobj.get_object().values() if v.get_object().get("/Subtype") == "/Image")
    except Exception:
        return 0


def _outline_titles(reader: PdfReader) -> list[str]:
    out: list[str] = []

    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
            else:
                out.append(_norm(getattr(it, "title", "") or ""))

    try:
        walk(reader.outline)
    except Exception:
        pass
    return out


def validate_pdf(path: str | Path, doc: PdfDoc, requested_pages: int | None = None) -> ValidationResult:
    result = ValidationResult()
    if not check_file_basic(path, result):
        return result
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            result.add("critical", "encrypted", "The PDF is encrypted.")
            return result
        pages = [p.extract_text() or "" for p in reader.pages]
    except Exception as exc:
        result.add("critical", "cannot_open", f"The PDF could not be opened ({type(exc).__name__}).")
        return result
    if not pages:
        result.add("critical", "no_pages", "The PDF has no pages.")
        return result

    full = _norm(" ".join(pages))
    if _norm(doc.title) not in _norm(pages[0]):
        result.add("critical", "title_missing", "The document title is not on the first page.")

    outline = _outline_titles(reader)
    for sec in doc.sections:
        h = _norm(sec.heading)
        if h not in full:
            result.add("critical", "section_missing", f"Required section '{sec.heading}' was not found in the PDF.")
        elif h not in outline:
            result.add("warning", "outline_missing", f"Section '{sec.heading}' is not in the PDF bookmarks.")

    for sec in doc.sections:
        for block in sec.blocks:
            if isinstance(block, TableBlock):
                first_hdr = _norm(str(block.table.columns[0]))
                if first_hdr not in full:
                    result.add("critical", "table_missing", f"A table in section '{sec.heading}' did not render.")
    want_charts = sum(isinstance(b, ChartBlock) for s in doc.sections for b in s.blocks)
    got_charts = sum(_count_images(p) for p in reader.pages)
    if got_charts < want_charts:
        result.add("critical", "chart_missing", f"{want_charts} charts were planned but only {got_charts} images are in the PDF.")

    for i, (page, text) in enumerate(zip(reader.pages, pages), start=1):
        if len(text.strip()) < 25 and _count_images(page) == 0:
            result.add("warning", "blank_page", f"Page {i} is blank or nearly blank.")

    if requested_pages is not None and len(pages) != requested_pages:
        result.add("warning", "page_count", f"{requested_pages} pages were requested but the PDF has {len(pages)}.",
                   {"pages": len(pages), "target": requested_pages})
    return result
