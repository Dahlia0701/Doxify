"""Re-opens the generated .xlsx and checks it against the plan.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from renderers.xlsx_renderer import DASHBOARD, plan_formulas
from app.schemas import WorkbookDoc
from app.security import escape_cell
from openpyxl.chart import BarChart, LineChart
from validators.common import ValidationResult, check_file_basic

ERROR_VALUES = {"#REF!", "#DIV/0!", "#NAME?", "#VALUE!", "#N/A", "#NUM!", "#NULL!"}


def validate_xlsx(path: str | Path, doc: WorkbookDoc) -> ValidationResult:
    result = ValidationResult()
    if not check_file_basic(path, result):
        return result
    try:
        wb = load_workbook(path)
    except Exception as exc:  # corrupt zip, bad xml, etc.
        result.add("critical", "cannot_open", f"The workbook could not be opened ({type(exc).__name__}).")
        return result

    expected_sheets = [s.name for s in doc.sheets] + ([DASHBOARD] if doc.include_dashboard else [])
    for extra in set(wb.sheetnames) - set(expected_sheets):
        result.add("critical", "sheet_unexpected", f"Unexpected sheet '{extra}'.")
    for name in expected_sheets:
        if name not in wb.sheetnames:
            result.add("critical", "sheet_missing", f"Required sheet '{name}' is missing.")

    # headers and row counts
    for sheet in doc.sheets:
        if sheet.name not in wb.sheetnames:
            continue
        ws = wb[sheet.name]
        headers = [ws.cell(row=1, column=c).value for c in range(1, len(sheet.all_columns) + 1)]
        if headers != sheet.all_columns:
            result.add("critical", "headers_wrong", f"Sheet '{sheet.name}' headers do not match the plan.")
        data_rows = sum(
            1 for r in range(2, len(sheet.rows) + 2)
            if any(ws.cell(row=r, column=c).value is not None for c in range(1, len(sheet.columns) + 1))
        )
        if sheet.rows and data_rows < len(sheet.rows):
            result.add("critical", "rows_missing", f"Sheet '{sheet.name}' has {data_rows} of {len(sheet.rows)} data rows.")

    # Compare actual source data against the planned data, not just row counts.
    for sheet in doc.sheets:
        if sheet.name not in wb.sheetnames:
            continue
        ws = wb[sheet.name]
        for r, values in enumerate(sheet.rows, start=2):
            for c, value in enumerate(values, start=1):
                actual = ws.cell(r, c).value
                expected_value = escape_cell(value)
                if not _same_value(actual, expected_value):
                    result.add("critical", "data_mismatch", f"{sheet.name}!{ws.cell(r,c).coordinate} does not match the planned value.")
        if sheet.totals_row and sheet.rows and not sheet.is_numeric(sheet.all_columns[0]):
            if ws.cell(len(sheet.rows)+2, 1).value != "Total":
                result.add("critical", "total_label", f"Sheet '{sheet.name}' is missing the totals label.")

    # formulas: the set in the file must equal the planned set
    expected = plan_formulas(doc)
    actual: dict[tuple[str, str], str] = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if cell.data_type == "f" or (isinstance(v, str) and v.startswith("=") and cell.data_type != "s"):
                    actual[(ws.title, cell.coordinate)] = str(v)
                if isinstance(v, str) and v.strip() in ERROR_VALUES:
                    result.add("critical", "error_value", f"{ws.title}!{cell.coordinate} contains {v}.")
    for key, formula in expected.items():
        if key[0] not in wb.sheetnames:
            continue
        if key not in actual:
            result.add("critical", "formula_missing", f"{key[0]}!{key[1]} should contain a formula but does not.")
        elif actual[key] != formula:
            result.add("critical", "formula_changed", f"{key[0]}!{key[1]} has an unexpected formula.")
    for key in actual:
        if key not in expected:
            result.add("critical", "formula_unexpected", f"{key[0]}!{key[1]} contains a formula that was not planned.")
    for key, formula in actual.items():
        if "#REF!" in formula:
            result.add("critical", "invalid_formula", f"{key[0]}!{key[1]} has a broken reference.")

    # charts
    chart_sheets = {DASHBOARD: doc.charts} if doc.include_dashboard else {}
    if not doc.include_dashboard:
        for ch in doc.charts:
            chart_sheets.setdefault(ch.sheet, []).append(ch)
    for sheet_name, specs in chart_sheets.items():
        if sheet_name not in wb.sheetnames:
            continue
        found = wb[sheet_name]._charts
        if len(found) != len(specs):
            result.add("critical", "chart_missing", f"Sheet '{sheet_name}' has {len(found)} charts; expected exactly {len(specs)}.")
        else:
            by_name = {s.name: s for s in doc.sheets}
            for chart, spec in zip(found, specs):
                if not isinstance(chart, BarChart if spec.kind == "bar" else LineChart):
                    result.add("critical", "chart_type", f"Chart '{spec.title}' has the wrong chart type.")
                _check_chart_ranges(chart, spec, by_name[spec.sheet], result)
    for ws in wb.worksheets:
        if ws.title not in chart_sheets and ws._charts:
            result.add("critical", "chart_unexpected", f"Unexpected chart on '{ws.title}'.")
    wb.close()
    return result


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _same_value(actual, expected) -> bool:
    """Numbers compare by value (Excel stores 11190.0 as 11190 and keeps ~15 significant digits);
    text, booleans and blanks must match exactly, including type (so '5' never equals 5)."""
    if _is_num(actual) and _is_num(expected):
        return math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
    return actual == expected and type(actual) is type(expected)


def _norm(ref: str | None) -> str | None:
    """'Monthly Data'!$B$2:$B$7 -> Monthly Data!B2:B7 (so quoting style does not matter)."""
    return ref.replace("$", "").replace("'", "") if ref else None


def _series_ref(part, kind: str) -> str | None:
    """Read the cell-range text from a chart series part (numRef or strRef)."""
    if part is None:
        return None
    ref = getattr(part, kind, None)
    return _norm(ref.f) if ref is not None else None


def _chart_title(chart) -> str | None:
    try:
        return "".join(run.t for para in chart.title.tx.rich.p for run in (para.r or []))
    except AttributeError:
        return None


def _check_chart_ranges(chart, spec, sheet, result: ValidationResult) -> None:
    """The chart must plot the planned columns, over every data row, with the planned labels."""
    last = max(len(sheet.rows) + 1, 2)  # same rule as the renderer
    letter = lambda col: get_column_letter(sheet.all_columns.index(col) + 1)  # noqa: E731

    if len(chart.series) != len(spec.value_columns):
        result.add("critical", "chart_series", f"Chart '{spec.title}' has {len(chart.series)} series, expected {len(spec.value_columns)}.")
        return

    title = _chart_title(chart)
    if title != spec.title:
        result.add("warning", "chart_title", f"Chart title is '{title}', expected '{spec.title}'.")

    want_cat = f"{sheet.name}!{letter(spec.category_column)}2:{letter(spec.category_column)}{last}"
    for series, col in zip(chart.series, spec.value_columns):
        L = letter(col)
        got_val = _series_ref(series.val, "numRef")
        if got_val != f"{sheet.name}!{L}2:{L}{last}":
            result.add("critical", "chart_range", f"Chart '{spec.title}': values for '{col}' point at {got_val}, expected {sheet.name}!{L}2:{L}{last}.")
        got_name = _series_ref(series.tx, "strRef")
        if got_name != f"{sheet.name}!{L}1":
            result.add("warning", "chart_series_name", f"Chart '{spec.title}': series name for '{col}' is not linked to its header.")
        cat = series.cat
        got_cat = _series_ref(cat, "numRef") or _series_ref(cat, "strRef")
        if got_cat != want_cat:
            result.add("critical", "chart_categories", f"Chart '{spec.title}': labels point at {got_cat}, expected {want_cat}.")


def recalculate_with_libreoffice(path: str | Path, timeout: int = 60) -> Path | None:
    """Optional: recalculate formulas so computed values can be read.

    Returns the path of a recalculated copy, or None if LibreOffice is unavailable
    or fails. Callers must treat None as 'could not check', not as 'passed'.
    """
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    out_dir = Path(tempfile.mkdtemp(prefix="recalc_"))
    try:
        subprocess.run(
            [exe, "--headless", "--convert-to", "xlsx", "--outdir", str(out_dir), str(path)],
            check=True, capture_output=True, timeout=timeout,
        )
    except Exception:
        shutil.rmtree(out_dir, ignore_errors=True)
        return None
    out = out_dir / Path(path).name
    if out.exists():
        return out
    shutil.rmtree(out_dir, ignore_errors=True)
    return None


def check_calculated_values(path: str | Path, result: ValidationResult, timeout: int = 60) -> bool:
    """Recalculate and scan for error values. Returns False if the check could not run.

    The temporary directory made by the recalculation is always removed.
    """
    recalculated = recalculate_with_libreoffice(path, timeout=timeout)
    if recalculated is None:
        return False
    try:
        wb = load_workbook(recalculated, data_only=True)
        for ws in wb.worksheets:
            for row in ws.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str) and cell.value.strip() in ERROR_VALUES:
                        result.add("critical", "calc_error", f"{ws.title}!{cell.coordinate} evaluates to {cell.value}.")
        wb.close()
    finally:
        shutil.rmtree(recalculated.parent, ignore_errors=True)
    return True
