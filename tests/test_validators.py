import zipfile

from openpyxl import load_workbook

from validators.xlsx_validator import validate_xlsx


def codes(result):
    return {i.code for i in result.critical}


def test_good_file_passes(xlsx_path, doc):
    result = validate_xlsx(xlsx_path, doc)
    assert result.status == "completed", [str(i) for i in result.issues]


def test_missing_file_is_critical(doc, tmp_path):
    result = validate_xlsx(tmp_path / "nope.xlsx", doc)
    assert result.status == "failed"
    assert "file_missing" in codes(result)


def test_empty_file_is_critical(doc, tmp_path):
    p = tmp_path / "empty.xlsx"
    p.write_bytes(b"")
    assert "file_empty" in codes(validate_xlsx(p, doc))


def test_corrupt_file_is_critical(doc, tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"this is not a zip file")
    result = validate_xlsx(p, doc)
    assert result.status == "failed"
    assert "cannot_open" in codes(result)


def test_truncated_zip_is_critical(xlsx_path, doc):
    data = xlsx_path.read_bytes()
    xlsx_path.write_bytes(data[: len(data) // 2])
    assert validate_xlsx(xlsx_path, doc).status == "failed"


def test_missing_sheet_is_critical(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    del wb["Monthly Data"]
    wb.save(xlsx_path)
    assert "sheet_missing" in codes(validate_xlsx(xlsx_path, doc))


def test_tampered_formula_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    wb["Monthly Data"]["D3"] = '=HYPERLINK("http://evil.test")'
    wb.save(xlsx_path)
    assert "formula_changed" in codes(validate_xlsx(xlsx_path, doc))


def test_extra_formula_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    wb["Monthly Data"]["H2"] = "=1+1"
    wb.save(xlsx_path)
    assert "formula_unexpected" in codes(validate_xlsx(xlsx_path, doc))


def test_removed_formula_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    wb["Monthly Data"]["D4"] = 99
    wb.save(xlsx_path)
    assert "formula_missing" in codes(validate_xlsx(xlsx_path, doc))


def test_error_value_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    ws = wb["Monthly Data"]
    ws["A5"].value = "#REF!"
    ws["A5"].data_type = "s"
    wb.save(xlsx_path)
    assert "error_value" in codes(validate_xlsx(xlsx_path, doc))


def test_missing_chart_is_critical(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    wb["Dashboard"]._charts = []
    wb.save(xlsx_path)
    assert "chart_missing" in codes(validate_xlsx(xlsx_path, doc))


def test_chart_pointing_at_wrong_range_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    chart = wb["Dashboard"]._charts[0]
    chart.series[0].val.numRef.f = "'Monthly Data'!$B$2:$B$4"  # only 3 of 6 months
    wb.save(xlsx_path)
    assert "chart_range" in codes(validate_xlsx(xlsx_path, doc))


def test_chart_with_wrong_labels_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    chart = wb["Dashboard"]._charts[0]
    cat = chart.series[0].cat
    (cat.numRef or cat.strRef).f = "'Monthly Data'!$B$2:$B$7"  # numbers instead of months
    wb.save(xlsx_path)
    assert "chart_categories" in codes(validate_xlsx(xlsx_path, doc))


def test_chart_missing_a_series_is_caught(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    wb["Dashboard"]._charts[0].series.pop()
    wb.save(xlsx_path)
    assert "chart_series" in codes(validate_xlsx(xlsx_path, doc))


def test_wrong_chart_title_is_only_a_warning(xlsx_path, doc):
    wb = load_workbook(xlsx_path)
    wb["Dashboard"]._charts[0].title = "Something else"
    wb.save(xlsx_path)
    result = validate_xlsx(xlsx_path, doc)
    assert not result.critical
    assert result.status == "completed_with_warnings"
