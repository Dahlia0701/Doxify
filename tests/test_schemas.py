import json

import pytest
from pydantic import ValidationError

from app.planner import PlanError, parse_plan
from app.schemas import WorkbookDoc


def test_valid_plan_parses(plan_dict):
    assert parse_plan(json.dumps(plan_dict)).title == plan_dict["title"]


def test_fenced_json_is_accepted(plan_dict):
    raw = "```json\n" + json.dumps(plan_dict) + "\n```"
    assert parse_plan(raw).sheets[0].name == "Monthly Data"


def test_invalid_json_gives_clear_error():
    with pytest.raises(PlanError, match="valid JSON"):
        parse_plan("this is not json {")


def test_wrong_schema_gives_clear_error(plan_dict):
    plan_dict["sheets"][0]["rows"][0] = ["Jan", 1]  # one value short
    with pytest.raises(PlanError, match="invalid"):
        parse_plan(json.dumps(plan_dict))


def test_raw_formula_field_is_rejected(plan_dict):
    plan_dict["sheets"][0]["computed_columns"][0]["formula"] = "=INDIRECT(A1)"
    with pytest.raises(ValidationError):
        WorkbookDoc.model_validate(plan_dict)


def test_unknown_formula_type_rejected(plan_dict):
    plan_dict["sheets"][0]["computed_columns"][0]["formula_type"] = "macro"
    with pytest.raises(ValidationError):
        WorkbookDoc.model_validate(plan_dict)


def test_unknown_column_in_formula_rejected(plan_dict):
    plan_dict["sheets"][0]["computed_columns"][0]["columns"] = ["Revenue", "Nope"]
    with pytest.raises(ValidationError):
        WorkbookDoc.model_validate(plan_dict)


def test_duplicate_sheet_names_rejected(plan_dict):
    plan_dict["sheets"].append(dict(plan_dict["sheets"][0], name="monthly data"))
    with pytest.raises(ValidationError):
        WorkbookDoc.model_validate(plan_dict)


def test_chart_with_unknown_sheet_rejected(plan_dict):
    plan_dict["charts"][0]["sheet"] = "Ghost"
    with pytest.raises(ValidationError):
        WorkbookDoc.model_validate(plan_dict)
