"""creates pdf sections with headings, paragraphs, bullets, tables and charts
"""
from __future__ import annotations

import datetime as _dt
import io
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from app.schemas import ParagraphBlock, BulletsBlock, TableBlock, ChartBlock, PdfDoc, TableData
from renderers import theme
from renderers.charts import chart_png

PRIMARY = colors.HexColor("#" + theme.PRIMARY)
ACCENT = colors.HexColor("#" + theme.ACCENT)
LIGHT = colors.HexColor("#" + theme.LIGHT)
TEXT = colors.HexColor("#" + theme.TEXT)
PAGE_W, PAGE_H = A4
MARGIN = 20 * mm
FRAME_W = PAGE_W - 2 * MARGIN


def _styles(scale: float) -> dict[str, ParagraphStyle]:
    def ps(name, **kw):
        kw.setdefault("fontName", "Helvetica")
        kw.setdefault("textColor", TEXT)
        kw.setdefault("alignment", TA_LEFT)
        return ParagraphStyle(name, **kw)

    b = 10.5 * scale
    return {
        "title": ps("DxTitle", fontName="Helvetica-Bold", fontSize=26 * scale, leading=32 * scale, textColor=PRIMARY, spaceAfter=6),
        "subtitle": ps("DxSub", fontSize=13 * scale, leading=18 * scale, textColor=ACCENT, spaceAfter=4),
        "meta": ps("DxMeta", fontSize=9 * scale, leading=12 * scale, textColor=colors.HexColor("#666666"), spaceAfter=14),
        "h1": ps("DxH1", fontName="Helvetica-Bold", fontSize=16 * scale, leading=20 * scale, textColor=PRIMARY, spaceBefore=14 * scale, spaceAfter=6),
        "body": ps("DxBody", fontSize=b, leading=b * 1.45, spaceAfter=6 * scale),
        "bullet": ps("DxBullet", fontSize=b, leading=b * 1.4, leftIndent=14, bulletIndent=3, spaceAfter=2 * scale),
        "cell": ps("DxCell", fontSize=8.5 * scale, leading=11 * scale),
        "cellh": ps("DxCellH", fontName="Helvetica-Bold", fontSize=8.5 * scale, leading=11 * scale, textColor=colors.white),
        "caption": ps("DxCaption", fontName="Helvetica-Oblique", fontSize=8.5 * scale, leading=11 * scale, textColor=colors.HexColor("#666666"), spaceAfter=8 * scale),
        "toc": ps("DxToc", fontSize=b, leading=b * 1.6),
    }


def _p(text, style) -> Paragraph:
    return Paragraph(escape(str(text)), style)


def _fmt_cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    if isinstance(v, int) and not isinstance(v, bool):
        return f"{v:,}" if abs(v) >= 10_000 else str(v)
    return str(v)


def _table(data: TableData, st: dict) -> list:
    n = len(data.columns)
    head = [_p(c, st["cellh"]) for c in data.columns]
    rows = [[_p(_fmt_cell(v), st["cell"]) for v in r] for r in data.rows]
    # column widths proportional to the longest text (bounded) so wide tables stay on the page
    lens = []
    for c in range(n):
        longest = max([len(str(data.columns[c]))] + [len(_fmt_cell(r[c])) for r in data.rows])
        lens.append(min(max(longest, 6), 40))
    total = sum(lens)
    widths = [FRAME_W * l / total for l in lens]
    t = Table([head] + rows, colWidths=widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#C8D3E0")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    for i in range(1, len(rows) + 1):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(TableStyle(style))
    out: list = [t]
    if data.caption:
        out.append(_p(data.caption, st["caption"]))
    else:
        out.append(Spacer(1, 8))
    return out


class _Doc(BaseDocTemplate):
    def __init__(self, buf, title: str, **kw):
        super().__init__(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN + 4 * mm,
                         bottomMargin=MARGIN, title=title, author="Doxify", **kw)
        frame = Frame(MARGIN, MARGIN, FRAME_W, PAGE_H - 2 * MARGIN - 4 * mm, id="main")
        self.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=self._decorate)])
        self._title = title

    def _decorate(self, canv, doc):
        canv.saveState()
        canv.setFont("Helvetica", 8)
        canv.setFillColor(colors.HexColor("#888888"))
        canv.drawString(MARGIN, 10 * mm, self._title[:80])
        canv.drawRightString(PAGE_W - MARGIN, 10 * mm, f"Page {doc.page}")
        canv.setStrokeColor(ACCENT)
        canv.setLineWidth(1.2)
        canv.line(MARGIN, PAGE_H - MARGIN + 2 * mm, PAGE_W - MARGIN, PAGE_H - MARGIN + 2 * mm)
        canv.restoreState()

    def afterFlowable(self, flowable):
        key = getattr(flowable, "_dx_heading", None)
        if key is not None:
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(flowable.getPlainText(), key, 0, 0)
            self.notify("TOCEntry", (0, escape(flowable.getPlainText()), self.page))  # TOC parses markup


def _build_story(doc: PdfDoc, today: str) -> list:
    st = _styles(doc.font_scale)
    story: list = [Spacer(1, 6 * mm), _p(doc.title, st["title"])]
    if doc.subtitle:
        story.append(_p(doc.subtitle, st["subtitle"]))
    story.append(_p(f"Prepared for {doc.audience}  •  {today}", st["meta"]))
    if doc.include_toc:
        toc = TableOfContents()
        toc.levelStyles = [st["toc"]]
        story += [_p("Contents", st["h1"]), toc, Spacer(1, 10)]
    for i, sec in enumerate(doc.sections):
        if i > 0 and doc.section_breaks:
            story.append(PageBreak())
        h = _p(sec.heading, st["h1"])
        h._dx_heading = f"sec{i}"
        first = True
        for block in sec.blocks:
            parts: list
            if isinstance(block, ParagraphBlock):
                parts = [_p(block.text, st["body"])]
            elif isinstance(block, BulletsBlock):
                parts = [Paragraph(escape(it), st["bullet"], bulletText="•") for it in block.items]
            elif isinstance(block, TableBlock):
                parts = _table(block.table, st)
                if len(block.table.rows) <= 15:
                    parts = [KeepTogether(parts)]  # small tables stay on one page
            else:  # ChartBlock
                png = chart_png(block.chart)
                img = Image(io.BytesIO(png), width=FRAME_W * 0.85, height=FRAME_W * 0.85 * 3.2 / 6.2)
                img.hAlign = "CENTER"
                parts = [img, Spacer(1, 8)]
            if first:
                story.append(KeepTogether([h] + parts[:1]))  # never strand a heading at a page bottom
                story += parts[1:]
                first = False
            else:
                story += parts
    if doc.assumptions:
        story.append(_p("Assumptions and notes", st["h1"]))
        story += [Paragraph(escape(a), st["bullet"], bulletText="•") for a in doc.assumptions]
    return story


def render_pdf(doc: PdfDoc, path: str | Path, today: str | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    today = today or _dt.date.today().strftime("%d %B %Y")
    buf = io.BytesIO()
    pdf = _Doc(buf, doc.title)
    pdf.multiBuild(_build_story(doc, today))  # multiBuild so the TOC gets real page numbers
    path.write_bytes(buf.getvalue())
    return path
