""" excel generation, formual,totals,charts and dashboards"""
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
    assert len(dash._charts) == 2
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
    assert len(wb["Monthly Data"]._charts) == 2


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


def test_margin_total_is_overall_ratio_not_sum_of_percentages(xlsx_path):
    ws = load_workbook(xlsx_path)["Monthly Data"]
    assert ws["D8"].value == "=B8-C8"  # total profit = total revenue - total expenses
    assert ws["E8"].value == '=IF(B8=0,"",D8/B8)'  # overall margin, not =SUM(E2:E7)


def test_dashboard_kpi_for_margin_points_at_totals_row(xlsx_path):
    dash = load_workbook(xlsx_path)["Dashboard"]
    labels = [dash.cell(row=r, column=1).value for r in range(4, 8)]
    assert labels[:3] == ["Total Revenue", "Total Expenses", "Total Profit"]


def test_dashboard_never_sums_a_percentage(doc, tmp_path):
    from app.schemas import ChartSpec

    doc.charts.append(
        ChartSpec(kind="line", title="Margin", sheet="Monthly Data",
                  category_column="Month", value_columns=["Margin"])
    )
    path = render_xlsx(doc, tmp_path / "m.xlsx")
    dash = load_workbook(path)["Dashboard"]
    row = [r for r in range(4, 12) if dash.cell(row=r, column=1).value == "Overall Margin"]
    assert row, "expected an 'Overall Margin' KPI"
    assert dash.cell(row=row[0], column=2).value == "='Monthly Data'!E8"
    assert dash.cell(row=row[0], column=2).number_format == "0.0%"


def test_margin_kpi_skipped_without_totals_row(doc, tmp_path):
    from app.schemas import ChartSpec

    doc.sheets[0].totals_row = False
    doc.charts.append(
        ChartSpec(kind="line", title="Margin", sheet="Monthly Data",
                  category_column="Month", value_columns=["Margin"])
    )
    dash = load_workbook(render_xlsx(doc, tmp_path / "n.xlsx"))["Dashboard"]
    labels = [dash.cell(row=r, column=1).value for r in range(4, 12)]
    assert "Overall Margin" not in labels and "Total Margin" not in labels


def test_average_column_total_is_an_average(doc, tmp_path):
    from app.schemas import ComputedColumn

    doc.sheets[0].computed_columns.append(
        ComputedColumn(name="Avg", formula_type="average", columns=["Revenue", "Expenses"])
    )
    ws = load_workbook(render_xlsx(doc, tmp_path / "a.xlsx"))["Monthly Data"]
    assert ws["F8"].value == "=AVERAGE(F2:F7)"
