""" formual injection protection and formula allowlist"""
import pytest

from app.security import SecurityError, build_formula, escape_cell


@pytest.mark.parametrize("bad", ['=HYPERLINK("http://evil.test")', "+1+1", "-cmd|' /C calc'!A0", "@SUM(1)", "\t=1+1"])
def test_formula_like_text_is_escaped(bad):
    out = escape_cell(bad)
    assert out == "'" + bad


def test_real_numbers_stay_numbers():
    assert escape_cell("-5") == -5
    assert escape_cell("+3.5") == 3.5
    assert escape_cell(12) == 12
    assert escape_cell(None) is None


def test_plain_text_unchanged():
    assert escape_cell("January") == "January"


def test_formula_builder_uses_allowlist():
    assert build_formula("difference", ["B2", "C2"]) == "=B2-C2"
    assert build_formula("sum", ["B2", "C2"]) == "=SUM(B2,C2)"


def test_disallowed_formula_type_rejected():
    with pytest.raises(SecurityError):
        build_formula("INDIRECT", ["B2"])


def test_wrong_number_of_inputs_rejected():
    with pytest.raises(SecurityError):
        build_formula("difference", ["B2"])
