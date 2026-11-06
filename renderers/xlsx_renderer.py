"""creates excel sheets with data, formulas, totals and charts
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
    """Kept for readability; the real rule lives in SheetSpec.is_numeric."""
    return sheet.is_numeric(name)


def _col_letter(sheet: SheetSpec, name: str) -> str:
    return get_column_letter(sheet.all_columns.index(name) + 1)


def dashboard_kpis(doc: WorkbookDoc) -> list[tuple[str, str, str]]:
    """KPI tiles for the dashboard: (label, formula, number_format_key).

    Rules (so no KPI is a meaningless sum):
    - ratio / growth columns -> 'Overall X', taken from the sheet's totals row (skipped if none)
    - average columns        -> 'Average X'
    - everything else        -> 'Total X'
    """
    by_name = {s.name: s for s in doc.sheets}
    pairs: list[tuple[str, str]] = []
    for ch in doc.charts:
        for col in ch.value_columns:
            if (ch.sheet, col) not in pairs:
                pairs.append((ch.sheet, col))
    if not pairs:
        first = doc.sheets[0]
        pairs = [(first.name, c) for c in first.all_columns if first.is_numeric(c)]

    kpis: list[tuple[str, str, str]] = []
    for sheet_name, col in pairs:
        sheet = by_name[sheet_name]
        n = len(sheet.rows)
        if not n or not sheet.is_numeric(col):
            continue
        L = _col_letter(sheet, col)
        q = quote_sheet(sheet_name)
        cc = next((c for c in sheet.computed_columns if c.name == col), None)
        if cc is not None and cc.formula_type == "growth":
            continue  # no meaningful overall growth from summed input columns
        if cc is not None and cc.formula_type == "ratio":
            if not sheet.totals_row:
                continue
            kpis.append((f"Overall {col}", f"={q}!{L}{n + 2}", cc.number_format))
        elif cc is not None and cc.formula_type == "average":
            kpis.append((f"Average {col}", f"=AVERAGE({q}!{L}2:{L}{n + 1})", cc.number_format))
        else:
            fmt = cc.number_format if cc is not None else "number"
            kpis.append((f"Total {col}", f"=SUM({q}!{L}2:{L}{n + 1})", fmt))
        if len(kpis) >= MAX_KPIS:
            break
    return kpis


def plan_formulas(doc: WorkbookDoc) -> dict[tuple[str, str], str]:
    """Every formula that must exist in the workbook: {(sheet, 'B2'): '=...'}.

    Totals row rules:
    - base number columns        -> SUM of the column
    - computed average columns   -> AVERAGE of the column
    - other computed columns     -> the same formula applied to the totals row
      (so Margin total = total Profit / total Revenue, never a sum of percentages)
    """
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
            total_row = n + 2
            for c in sheet.columns:
                if sheet.is_numeric(c):
                    L = _col_letter(sheet, c)
                    out[(sheet.name, f"{L}{total_row}")] = f"=SUM({L}2:{L}{n + 1})"
            for k, cc in enumerate(sheet.computed_columns):
                if not sheet.is_numeric(cc.name):
                    continue
                letter = get_column_letter(base + k + 1)
                if cc.formula_type == "growth":
                    continue  # avoid misleading totals for growth
                if cc.formula_type == "average":
                    formula = f"=AVERAGE({letter}2:{letter}{n + 1})"
                else:
                    refs = [f"{_col_letter(sheet, c)}{total_row}" for c in cc.columns]
                    formula = build_formula(cc.formula_type, refs)
                out[(sheet.name, f"{letter}{total_row}")] = formula
    if doc.include_dashboard:
        for i, (_label, formula, _fmt) in enumerate(dashboard_kpis(doc)):
            out[(DASHBOARD, f"B{4 + i}")] = formula
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
    wb.remove(wb.active)
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
        for i, (label, _formula, fmt) in enumerate(dashboard_kpis(doc)):
            _write_value(dash.cell(row=4 + i, column=1), label)
            dash.cell(row=4 + i, column=2).number_format = theme.FORMATS[fmt]
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
