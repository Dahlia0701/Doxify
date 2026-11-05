"""Re-opens the generated .xlsx and checks it against the plan.

Checks: opens, sheets, headers, row counts, formulas (exactly the planned ones),
error values, charts, dashboard. Note: openpyxl does not calculate formulas, so
computed values are only checked by the optional LibreOffice recalculation.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from openpyxl import load_workbook

from renderers.xlsx_renderer import DASHBOARD, plan_formulas
from app.schemas import WorkbookDoc
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
        found = getattr(wb[sheet_name], "_charts")
        if len(found) < len(specs):
            result.add("critical", "chart_missing", f"Sheet '{sheet_name}' has {len(found)} of {len(specs)} charts.")
        else:
            for chart, spec in zip(found, specs):
                if len(chart.series) != len(spec.value_columns):
                    result.add("warning", "chart_series", f"Chart '{spec.title}' has {len(chart.series)} series, expected {len(spec.value_columns)}.")
    return result


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
        return None
    out = out_dir / Path(path).name
    return out if out.exists() else None


def check_calculated_values(path: str | Path, result: ValidationResult) -> bool:
    """Recalculate and scan for error values. Returns False if the check could not run."""
    recalculated = recalculate_with_libreoffice(path)
    if recalculated is None:
        return False
    wb = load_workbook(recalculated, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.strip() in ERROR_VALUES:
                    result.add("critical", "calc_error", f"{ws.title}!{cell.coordinate} evaluates to {cell.value}.")
    return True
