"""Builds an .xlsx from a WorkbookDoc. No LLM and no LLM-written formulas here.

`plan_formulas` is the single source of truth for every formula in the file.
The validator reuses it to detect any formula that should not be there.
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from renderers import theme
from app.schemas import SheetSpec, WorkbookDoc
from app.security import build_formula, escape_cell, quote_sheet

DASHBOARD = "Dashboard"
MAX_KPIS = 8


def is_numeric_column(sheet: SheetSpec, name: str) -> bool:
    """Computed columns are numeric. Base columns are numeric if every value is a number."""
    if name not in sheet.columns:
        return True
    idx = sheet.columns.index(name)
    values = [r[idx] for r in sheet.rows if r[idx] not in (None, "")]
    if not values:
        return False
    for v in values:
        if isinstance(v, bool):
            return False
        if isinstance(v, (int, float)):
            continue
        if isinstance(v, str) and isinstance(escape_cell(v), (int, float)):
            continue
        return False
    return True


def _col_letter(sheet: SheetSpec, name: str) -> str:
    return get_column_letter(sheet.all_columns.index(name) + 1)


def dashboard_kpis(doc: WorkbookDoc) -> list[tuple[str, tuple[str, str]]]:
    """(label, sheet name + column) pairs shown as KPI totals on the dashboard."""
    by_name = {s.name: s for s in doc.sheets}
    pairs: list[tuple[str, str]] = []
    for ch in doc.charts:
        for col in ch.value_columns:
            if (ch.sheet, col) not in pairs:
                pairs.append((ch.sheet, col))
    if not pairs:
        first = doc.sheets[0]
        pairs = [(first.name, c) for c in first.all_columns if is_numeric_column(first, c)]
    return [
        (f"Total {col}", (sheet, col))
        for sheet, col in pairs[:MAX_KPIS]
        if by_name[sheet].rows
    ]


def plan_formulas(doc: WorkbookDoc) -> dict[tuple[str, str], str]:
    """Every formula that must exist in the workbook: {(sheet, 'B2'): '=...'}."""
    out: dict[tuple[str, str], str] = {}
    for sheet in doc.sheets:
        n = len(sheet.rows)
        base = len(sheet.columns)
        for k, cc in enumerate(sheet.computed_columns):
            letter = get_column_letter(base + k + 1)
            for r in range(2, n + 2):
                refs = [f"{_col_letter(sheet, c)}{r}" for c in cc.columns]
                out[(sheet.name, f"{letter}{r}")] = build_formula(cc.formula_type, refs)
        if sheet.totals_row and n:
            for c in sheet.all_columns:
                if is_numeric_column(sheet, c):
                    L = _col_letter(sheet, c)
                    out[(sheet.name, f"{L}{n + 2}")] = f"=SUM({L}2:{L}{n + 1})"
    if doc.include_dashboard:
        by_name = {s.name: s for s in doc.sheets}
        for i, (_, (sheet_name, col)) in enumerate(dashboard_kpis(doc)):
            sheet = by_name[sheet_name]
            L = _col_letter(sheet, col)
            n = len(sheet.rows)
            out[(DASHBOARD, f"B{4 + i}")] = f"=SUM({quote_sheet(sheet_name)}!{L}2:{L}{n + 1})"
    return out


def _write_value(cell, value) -> None:
    value = escape_cell(value)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"  # force text so it can never be read as a formula


def _style_header(ws, ncols: int) -> None:
    fill = PatternFill("solid", start_color=theme.PRIMARY)
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(name=theme.FONT, bold=True, color=theme.WHITE)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.freeze_panes = "A2"


def _autosize(ws) -> None:
    widths: dict[int, int] = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None:
                text = str(cell.value)
                length = 12 if text.startswith("=") else len(text)
                widths[cell.column] = max(widths.get(cell.column, 8), length + 2)
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = min(w, 45)


def _fill_sheet(ws, sheet: SheetSpec, formulas: dict[tuple[str, str], str]) -> None:
    all_cols = sheet.all_columns
    for c, name in enumerate(all_cols, start=1):
        _write_value(ws.cell(row=1, column=c), name)
    _style_header(ws, len(all_cols))
    n = len(sheet.rows)
    for r, row in enumerate(sheet.rows, start=2):
        for c, value in enumerate(row, start=1):
            _write_value(ws.cell(row=r, column=c), value)

    cur = set(sheet.currency_columns)
    fmt_for: dict[str, str] = {c: theme.FORMATS["currency"] for c in cur}
    for cc in sheet.computed_columns:
        fmt_for[cc.name] = theme.FORMATS[cc.number_format]

    for (sname, coord), formula in formulas.items():
        if sname == sheet.name:
            ws[coord].value = formula

    if sheet.totals_row and n:
        total_row = n + 2
        first = ws.cell(row=total_row, column=1)
        if not is_numeric_column(sheet, all_cols[0]):
            _write_value(first, "Total")
        for c in range(1, len(all_cols) + 1):
            ws.cell(row=total_row, column=c).font = Font(name=theme.FONT, bold=True)

    for c, name in enumerate(all_cols, start=1):
        fmt = fmt_for.get(name)
        if fmt:
            last = n + 2 if sheet.totals_row and n else n + 1
            for r in range(2, last + 1):
                ws.cell(row=r, column=c).number_format = fmt
    _autosize(ws)


def _make_chart(spec, data_ws, sheet: SheetSpec):
    chart = BarChart() if spec.kind == "bar" else LineChart()
    chart.title = spec.title
    chart.height, chart.width = 8, 16
    n = len(sheet.rows)
    last = max(n + 1, 2)
    for col in spec.value_columns:
        idx = sheet.all_columns.index(col) + 1
        chart.add_data(Reference(data_ws, min_col=idx, min_row=1, max_row=last), titles_from_data=True)
    cat = sheet.all_columns.index(spec.category_column) + 1
    chart.set_categories(Reference(data_ws, min_col=cat, min_row=2, max_row=last))
    return chart


def render_xlsx(doc: WorkbookDoc, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    formulas = plan_formulas(doc)

    wb = Workbook()
    wb.remove(wb.worksheets[0])
    wb.properties.title = doc.title
    sheets_by_name = {}
    for sheet in doc.sheets:
        ws = wb.create_sheet(sheet.name)
        _fill_sheet(ws, sheet, formulas)
        sheets_by_name[sheet.name] = (ws, sheet)

    dash = None
    if doc.include_dashboard:
        dash = wb.create_sheet(DASHBOARD, 0)
        dash["A1"].value = doc.title
        dash["A1"].font = Font(name=theme.FONT, size=16, bold=True, color=theme.PRIMARY)
        dash["A3"].value = "Key totals"
        dash["A3"].font = Font(name=theme.FONT, bold=True)
        for i, (label, _) in enumerate(dashboard_kpis(doc)):
            _write_value(dash.cell(row=4 + i, column=1), label)
            dash.cell(row=4 + i, column=2).number_format = theme.FORMATS["number"]
        for (sname, coord), formula in formulas.items():
            if sname == DASHBOARD:
                dash[coord].value = formula
        dash.column_dimensions["A"].width = 28
        dash.column_dimensions["B"].width = 18

    for i, spec in enumerate(doc.charts):
        data_ws, sheet = sheets_by_name[spec.sheet]
        chart = _make_chart(spec, data_ws, sheet)
        if dash is not None:
            dash.add_chart(chart, f"D{3 + i * 18}")
        else:
            data_ws.add_chart(chart, f"{get_column_letter(len(sheet.all_columns) + 2)}{2 + i * 18}")

    wb.save(path)
    return path
