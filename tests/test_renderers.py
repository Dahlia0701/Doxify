import shutil

import pandas as pd
import pytest
from openpyxl import load_workbook

from renderers.xlsx_renderer import render_xlsx
from validators.xlsx_validator import recalculate_with_libreoffice


def test_xlsx_opens_and_has_expected_sheets(xlsx_path):
    wb = load_workbook(xlsx_path)
    assert wb.sheetnames == ["Dashboard", "Monthly Data"]


def test_profit_formulas_are_built_by_python(xlsx_path):
    ws = load_workbook(xlsx_path)["Monthly Data"]
    assert ws["D2"].value == "=B2-C2"
    assert ws["D7"].value == "=B7-C7"
    assert ws["E2"].value == '=IF(B2=0,"",D2/B2)'
    assert ws["B8"].value == "=SUM(B2:B7)"  # totals row


def test_dashboard_has_charts_and_kpis(xlsx_path):
    dash = load_workbook(xlsx_path)["Dashboard"]
    assert len(getattr(dash, "_charts")) == 2
    assert dash["B4"].value.startswith("=SUM('Monthly Data'!")


def test_injected_cell_text_is_never_a_formula(doc, tmp_path):
    doc.sheets[0].rows[0][0] = '=HYPERLINK("http://evil.test","x")'
    path = render_xlsx(doc, tmp_path / "inj.xlsx")
    cell = load_workbook(path)["Monthly Data"]["A2"]
    assert cell.data_type == "s"
    assert cell.value.startswith("'=")


def test_no_dashboard_puts_charts_on_data_sheet(doc, tmp_path):
    doc.include_dashboard = False
    path = render_xlsx(doc, tmp_path / "nodash.xlsx")
    wb = load_workbook(path)
    assert "Dashboard" not in wb.sheetnames
    assert len(getattr(wb["Monthly Data"], "_charts")) == 2


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_computed_values_match_pandas(xlsx_path, doc):
    recalculated = recalculate_with_libreoffice(xlsx_path)
    assert recalculated is not None
    ws = load_workbook(recalculated, data_only=True)["Monthly Data"]
    df = pd.DataFrame(doc.sheets[0].rows, columns=doc.sheets[0].columns)
    expected_profit = (df["Revenue"] - df["Expenses"]).tolist()
    actual_profit = [ws.cell(row=r, column=4).value for r in range(2, 8)]
    assert actual_profit == pytest.approx(expected_profit)
    assert ws["B8"].value == pytest.approx(df["Revenue"].sum())
