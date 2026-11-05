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
    getattr(wb["Dashboard"], "_charts").clear()
    wb.save(xlsx_path)
    assert "chart_missing" in codes(validate_xlsx(xlsx_path, doc))
