import pytest
from app.intent import parse_intent
from app.limits import InputError


def test_explicit_slide_count():
    intent = parse_intent('Create a 10-slide presentation for executives', None, False)
    assert intent.fmt == 'pptx'
    assert intent.slide_count == 10
    assert intent.audience == 'executives'


def test_format_selector_wins():
    intent = parse_intent('Create an Excel workbook about revenue', 'pdf', False)
    assert intent.fmt == 'pdf'
    assert intent.assumptions


def test_csv_defaults_to_excel():
    assert parse_intent('Analyze these sales numbers', None, True).fmt == 'xlsx'


def test_unknown_format_rejected():
    with pytest.raises(InputError):
        parse_intent('Make a report', 'docx', False)
