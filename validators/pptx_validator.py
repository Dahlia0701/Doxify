"""Re-opens the generated .pptx and checks it against the plan.
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Emu

from app.schemas import DeckDoc
from renderers import theme
from renderers.fit import text_height_in
from renderers.pptx_renderer import CHART_TYPES
from validators.common import ValidationResult, check_file_basic

OVERFLOW_TOLERANCE = 1.05
MIN_BODY_PT = 12


def _paragraph_texts(shape) -> list[str]:
    return [p.text for p in shape.text_frame.paragraphs if p.text.strip()]


def _font_pt(shape, default: float = 18.0) -> float:
    sizes = [r.font.size.pt for p in shape.text_frame.paragraphs for r in p.runs if r.font.size]
    return max(sizes) if sizes else default


def validate_pptx(path: str | Path, doc: DeckDoc, requested_slides: int | None = None) -> ValidationResult:
    result = ValidationResult()
    if not check_file_basic(path, result):
        return result
    try:
        prs = Presentation(str(path))
    except Exception as exc:
        result.add("critical", "cannot_open", f"The presentation could not be opened ({type(exc).__name__}).")
        return result

    slides = list(prs.slides)
    if len(slides) != len(doc.slides):
        result.add("critical", "slide_count", f"The file has {len(slides)} slides; the plan has {len(doc.slides)}.")
        return result
    if requested_slides is not None and len(slides) != requested_slides:
        result.add("critical", "slide_count_requested",
                   f"{requested_slides} slides were requested but the deck has {len(slides)}.")

    sw, sh = prs.slide_width, prs.slide_height
    for i, (slide, spec) in enumerate(zip(slides, doc.slides), start=1):
        where = f"Slide {i}"
        title = slide.shapes.title.text_frame.text.strip() if slide.shapes.title is not None else ""
        if title != spec.title.strip():
            result.add("critical", "slide_title", f"{where} title is '{title[:40]}', expected '{spec.title[:40]}'.")

        text_shapes = [s for s in slide.shapes if s.has_text_frame and s != slide.shapes.title and s.text_frame.text.strip()]
        charts = [s for s in slide.shapes if getattr(s, "has_chart", False) and s.has_chart]
        tables = [s for s in slide.shapes if getattr(s, "has_table", False) and s.has_table]
        if not (text_shapes or charts or tables):
            result.add("critical", "empty_slide", f"{where} has no content.")

        if spec.layout == "chart":
            if len(charts) != 1:
                result.add("critical", "chart_missing", f"{where} should have 1 chart but has {len(charts)}.")
            else:
                ch = charts[0].chart
                want = CHART_TYPES[spec.chart.kind]
                if ch.chart_type != want:
                    result.add("critical", "chart_type", f"{where} chart type is {ch.chart_type}, expected {want}.")
                plot = ch.plots[0]
                if len(list(plot.series)) != len(spec.chart.series):
                    result.add("critical", "chart_series", f"{where} chart has the wrong number of series.")
                elif len(list(plot.categories)) != len(spec.chart.categories):
                    result.add("critical", "chart_categories", f"{where} chart has the wrong number of categories.")
                else:
                    for got, exp in zip(plot.series, spec.chart.series):
                        if [round(v, 6) for v in got.values] != [round(v, 6) for v in exp.values]:
                            result.add("critical", "chart_data", f"{where} chart values do not match the plan.")
                            break
        elif charts:
            result.add("critical", "chart_unexpected", f"{where} has a chart the plan did not ask for.")
        if spec.layout == "table":
            if len(tables) != 1:
                result.add("critical", "table_missing", f"{where} should have 1 table but has {len(tables)}.")
            else:
                t = tables[0].table
                if len(t.rows) != len(spec.table.rows) + 1 or len(t.columns) != len(spec.table.columns):
                    result.add("critical", "table_shape", f"{where} table dimensions do not match the plan.")
        if spec.layout == "kpis":
            values = {s.text_frame.text.strip() for s in text_shapes}
            for k in spec.kpis:
                if k.value not in values:
                    result.add("critical", "kpi_missing", f"{where} is missing KPI value '{k.value}'.")

        # layout sanity: shapes inside the slide; text within its box
        for shape in slide.shapes:
            if shape.width is None or shape.name == "Background":
                continue
            if shape.left < -Emu(10) or shape.top < -Emu(10) or shape.left + shape.width > sw + 10 or shape.top + shape.height > sh + 10:
                result.add("warning", "shape_off_slide", f"{where}: '{shape.name}' extends beyond the slide.")
        for shape in text_shapes:
            if shape.name in ("Background", "Slide number") or not shape.text_frame.word_wrap:
                continue
            paras = _paragraph_texts(shape)
            need = text_height_in(paras, _font_pt(shape), Emu(shape.width).inches - 0.2)
            if need > Emu(shape.height).inches * OVERFLOW_TOLERANCE and Emu(shape.top).inches + need > theme.SLIDE_H - 0.3:
                result.add("warning", "text_overflow", f"{where}: text in '{shape.name}' probably runs off the slide.", {"slide": i})
            elif need > Emu(shape.height).inches * OVERFLOW_TOLERANCE and _font_pt(shape) <= MIN_BODY_PT:
                result.add("warning", "text_overflow", f"{where}: text in '{shape.name}' is too long for its box at the minimum font size.", {"slide": i})
        if slide.shapes.title is not None:
            need = text_height_in([title], _font_pt(slide.shapes.title, 30), Emu(slide.shapes.title.width).inches - 0.2)
            if need > Emu(slide.shapes.title.height).inches * OVERFLOW_TOLERANCE:
                result.add("warning", "title_overflow", f"{where}: the title is too long to fit.", {"slide": i})
    return result
