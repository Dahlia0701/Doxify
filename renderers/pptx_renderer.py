"""creates slides with titles, bullets, tables and charts
"""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from app.schemas import ChartData, DeckDoc, SlideSpec
from renderers import theme
from renderers.fit import fit_font

BODY_TOP = theme.MARGIN + theme.TITLE_H + 0.2
BODY_W = theme.SLIDE_W - 2 * theme.MARGIN
BODY_H = theme.SLIDE_H - BODY_TOP - 0.6
MIN_PT, MAX_BODY_PT = 12, 24

CHART_TYPES = {"bar": XL_CHART_TYPE.COLUMN_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS, "pie": XL_CHART_TYPE.PIE}


def _rgb(hex_: str) -> RGBColor:
    return RGBColor.from_string(hex_)


def _set_title(slide, text: str, *, color: str = theme.PRIMARY, size: int = 30, top: float = theme.MARGIN,
               height: float = theme.TITLE_H, width: float = BODY_W) -> None:
    t = slide.shapes.title
    t.left, t.top, t.width, t.height = Inches(theme.MARGIN), Inches(top), Inches(width), Inches(height)
    tf = t.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.text = text
    size, _ = fit_font([text], width - 0.2, height, size, 20)
    for p in tf.paragraphs:
        p.alignment = PP_ALIGN.LEFT
        for r in p.runs:
            r.font.size, r.font.bold, r.font.name = Pt(size), True, theme.FONT
            r.font.color.rgb = _rgb(color)


def _textbox(slide, left, top, width, height, paragraphs, *, size, bullet=True, color=theme.TEXT, bold=False,
             align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, name=None):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    if name:
        box.name = name
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, text in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(6)
        run = p.add_run()
        run.text = ("•  " + text) if bullet else text
        run.font.size, run.font.name, run.font.bold = Pt(size), theme.FONT, bold
        run.font.color.rgb = _rgb(color)
    return box


def _bullets(slide, items, left, top, width, height, name="Body"):
    size, _ = fit_font([f"•  {b}" for b in items], width - 0.2, height, MAX_BODY_PT, MIN_PT)
    return _textbox(slide, left, top, width, height, items, size=size, name=name)


def _add_native_chart(slide, data: ChartData, left, top, width, height):
    cd = CategoryChartData()
    cd.categories = data.categories
    for s in data.series:
        cd.add_series(s.name, s.values)
    gf = slide.shapes.add_chart(CHART_TYPES[data.kind], Inches(left), Inches(top), Inches(width), Inches(height), cd)
    chart = gf.chart
    chart.has_title = True
    chart.chart_title.text_frame.text = data.title
    chart.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(14)
    chart.has_legend = len(data.series) > 1 or data.kind == "pie"
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
    colors = [theme.PRIMARY, theme.ACCENT, "F18F01", "6A994E", "BC4B51", "8E7DBE"]
    if data.kind == "bar" and all(v >= 0 for s in data.series for v in s.values):
        chart.value_axis.minimum_scale = 0  # bars must start at zero or growth looks exaggerated
    if data.kind != "pie":
        for i, ser in enumerate(chart.plots[0].series):
            fmt = ser.format
            if data.kind == "bar":
                fmt.fill.solid()
                fmt.fill.fore_color.rgb = _rgb(colors[i % len(colors)])
            else:
                fmt.line.color.rgb = _rgb(colors[i % len(colors)])
    return gf


def _add_table(slide, data, left, top, width, height):
    nrows, ncols = len(data.rows) + 1, len(data.columns)
    row_h = min(0.5, height / nrows)
    gf = slide.shapes.add_table(nrows, ncols, Inches(left), Inches(top), Inches(width), Inches(row_h * nrows))
    gf.name = "Table"
    tbl = gf.table
    font = 14 if nrows <= 8 else 12
    for c, name in enumerate(data.columns):
        cell = tbl.cell(0, c)
        cell.text = str(name)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(theme.PRIMARY)
        for p in cell.text_frame.paragraphs:
            for r in p.runs:
                r.font.size, r.font.bold, r.font.name = Pt(font), True, theme.FONT
                r.font.color.rgb = _rgb(theme.WHITE)
    for r_i, row in enumerate(data.rows, start=1):
        for c, v in enumerate(row):
            cell = tbl.cell(r_i, c)
            cell.text = "" if v is None else (f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v))
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(theme.LIGHT if r_i % 2 else theme.WHITE)
            for p in cell.text_frame.paragraphs:
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    p.alignment = PP_ALIGN.RIGHT
                for r in p.runs:
                    r.font.size, r.font.name = Pt(font), theme.FONT
                    r.font.color.rgb = _rgb(theme.TEXT)
    return gf


def _render_slide(prs, spec: SlideSpec, index: int, deck: DeckDoc) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    if spec.layout == "title":
        bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
        bg.name = "Background"
        bg.fill.solid()
        bg.fill.fore_color.rgb = _rgb(theme.PRIMARY)
        bg.line.fill.background()
        slide.shapes._spTree.remove(bg._element)
        slide.shapes._spTree.insert(2, bg._element)  # behind the title placeholder
        _set_title(slide, spec.title, color=theme.WHITE, size=40, top=2.3, height=1.6, width=BODY_W)
        if spec.subtitle:
            _textbox(slide, theme.MARGIN, 4.0, BODY_W, 0.9, [spec.subtitle], size=22, bullet=False,
                     color=theme.LIGHT, name="Subtitle")
        if deck.assumptions:
            _textbox(slide, theme.MARGIN, 6.6, BODY_W, 0.4,
                     [f"{len(deck.assumptions)} assumption(s) noted. See speaker notes."], size=12,
                     bullet=False, color=theme.LIGHT, name="Footnote")
    else:
        _set_title(slide, spec.title)
        bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(theme.MARGIN), Inches(theme.MARGIN + theme.TITLE_H),
                                     Inches(1.2), Inches(0.06))
        bar.name = "Accent"
        bar.fill.solid()
        bar.fill.fore_color.rgb = _rgb(theme.ACCENT)
        bar.line.fill.background()
        if spec.layout == "bullets":
            _bullets(slide, spec.bullets, theme.MARGIN, BODY_TOP, BODY_W, BODY_H)
        elif spec.layout == "two_column":
            col_w = (BODY_W - 0.4) / 2
            for k, (head, items) in enumerate(((spec.left_title, spec.left), (spec.right_title, spec.right))):
                x = theme.MARGIN + k * (col_w + 0.4)
                top = BODY_TOP
                if head:
                    _textbox(slide, x, top, col_w, 0.5, [head], size=20, bullet=False, bold=True, color=theme.ACCENT,
                             name=f"{'Left' if k == 0 else 'Right'} heading")
                    top += 0.55
                size, _ = fit_font([f"•  {b}" for b in items], col_w - 0.2, BODY_H - (top - BODY_TOP), 22, MIN_PT)
                _textbox(slide, x, top, col_w, BODY_H - (top - BODY_TOP), items, size=size,
                         name="Left body" if k == 0 else "Right body")
        elif spec.layout == "table":
            _add_table(slide, spec.table, theme.MARGIN, BODY_TOP, BODY_W, BODY_H - 0.4)
            if spec.table.caption:
                _textbox(slide, theme.MARGIN, theme.SLIDE_H - 0.9, BODY_W, 0.4, [spec.table.caption], size=12,
                         bullet=False, color="666666", name="Caption")
        elif spec.layout == "chart":
            if spec.bullets:
                cw = BODY_W * 0.62
                _add_native_chart(slide, spec.chart, theme.MARGIN, BODY_TOP, cw, BODY_H)
                _bullets(slide, spec.bullets, theme.MARGIN + cw + 0.3, BODY_TOP, BODY_W - cw - 0.3, BODY_H, name="Takeaways")
            else:
                _add_native_chart(slide, spec.chart, theme.MARGIN, BODY_TOP, BODY_W, BODY_H)
        elif spec.layout == "kpis":
            n = len(spec.kpis)
            gap = 0.3
            w = (BODY_W - gap * (n - 1)) / n
            for k, kpi in enumerate(spec.kpis):
                x = theme.MARGIN + k * (w + gap)
                card = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(BODY_TOP + 0.3), Inches(w), Inches(3.4))
                card.name = f"KPI card {k + 1}"
                card.fill.solid()
                card.fill.fore_color.rgb = _rgb(theme.LIGHT)
                card.line.fill.background()
                _textbox(slide, x, BODY_TOP + 0.6, w, 1.2, [kpi.value], size=40 if len(kpi.value) <= 9 else 30,
                         bullet=False, bold=True, color=theme.PRIMARY, align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE,
                         name=f"KPI value {k + 1}")
                _textbox(slide, x, BODY_TOP + 1.9, w, 0.6, [kpi.label], size=18, bullet=False, align=PP_ALIGN.CENTER,
                         name=f"KPI label {k + 1}")
                if kpi.note:
                    _textbox(slide, x, BODY_TOP + 2.5, w, 0.8, [kpi.note], size=14, bullet=False, color="666666",
                             align=PP_ALIGN.CENTER, name=f"KPI note {k + 1}")
        # slide number
        _textbox(slide, theme.SLIDE_W - 1.4, theme.SLIDE_H - 0.5, 0.8, 0.3, [str(index + 1)], size=11,
                 bullet=False, color="888888", align=PP_ALIGN.RIGHT, name="Slide number")
    notes = spec.notes
    if index == 0 and deck.assumptions:
        notes = (notes + "\n\n" if notes else "") + "Assumptions:\n" + "\n".join(f"- {a}" for a in deck.assumptions)
    if notes:
        slide.notes_slide.notes_text_frame.text = notes


def render_pptx(doc: DeckDoc, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(theme.SLIDE_W), Inches(theme.SLIDE_H)
    prs.core_properties.title = doc.title
    for i, spec in enumerate(doc.slides):
        _render_slide(prs, spec, i, doc)
    prs.save(path)
    return path
