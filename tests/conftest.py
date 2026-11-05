import json

import pytest

from app.llm import MOCK_XLSX_PLAN
from app.schemas import WorkbookDoc


@pytest.fixture
def plan_dict() -> dict:
    return json.loads(json.dumps(MOCK_XLSX_PLAN))  # deep copy


@pytest.fixture
def doc(plan_dict) -> WorkbookDoc:
    return WorkbookDoc.model_validate(plan_dict)


@pytest.fixture
def xlsx_path(doc, tmp_path):
    from renderers.xlsx_renderer import render_xlsx

    return render_xlsx(doc, tmp_path / "out.xlsx")
