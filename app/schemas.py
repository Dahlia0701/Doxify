"""The intermediate representation (IR): the only thing the LLM is allowed to produce.

Anything outside these models is rejected by Pydantic, so the LLM can never
inject raw formulas, code, or unknown block types.
"""
from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FormulaType = Literal["sum", "difference", "average", "ratio", "growth"]

# Number of input columns each formula type needs: (min, max)
FORMULA_ARITY: dict[str, tuple[int, int]] = {
    "sum": (1, 4),
    "average": (1, 4),
    "difference": (2, 2),
    "ratio": (2, 2),
    "growth": (2, 2),
}

BAD_SHEET_CHARS = set('[]:*?/\\')
MAX_ROWS = 5000
Scalar = Union[str, int, float, bool, None]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ComputedColumn(_Strict):
    name: str = Field(min_length=1, max_length=60)
    formula_type: FormulaType
    columns: list[str] = Field(min_length=1, max_length=4)
    number_format: Literal["number", "currency", "percent"] = "number"

    @model_validator(mode="after")
    def _check_arity(self) -> "ComputedColumn":
        lo, hi = FORMULA_ARITY[self.formula_type]
        if not lo <= len(self.columns) <= hi:
            raise ValueError(
                f"formula '{self.formula_type}' needs {lo}-{hi} columns, got {len(self.columns)}"
            )
        return self


class SheetSpec(_Strict):
    name: str = Field(min_length=1, max_length=31)
    columns: list[str] = Field(min_length=1, max_length=50)
    rows: list[list[Scalar]] = Field(default_factory=list, max_length=MAX_ROWS)
    computed_columns: list[ComputedColumn] = Field(default_factory=list, max_length=20)
    totals_row: bool = False
    currency_columns: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _sheet_name(cls, v: str) -> str:
        if any(ch in BAD_SHEET_CHARS for ch in v):
            raise ValueError(f"sheet name '{v}' has characters Excel does not allow")
        if v.strip() != v or v.startswith("'") or v.endswith("'"):
            raise ValueError("sheet name must not start/end with spaces or quotes")
        return v

    @property
    def all_columns(self) -> list[str]:
        return list(self.columns) + [c.name for c in self.computed_columns]

    @model_validator(mode="after")
    def _check(self) -> "SheetSpec":
        names = self.all_columns
        if any(not n.strip() for n in names):
            raise ValueError(f"sheet '{self.name}' has an empty column name")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError(f"sheet '{self.name}' has duplicate column names")
        for i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"sheet '{self.name}' row {i + 1} has {len(row)} values, "
                    f"expected {len(self.columns)}"
                )
        # computed columns may use base columns and earlier computed columns
        known = list(self.columns)
        for cc in self.computed_columns:
            for ref in cc.columns:
                if ref not in known:
                    raise ValueError(
                        f"computed column '{cc.name}' uses unknown column '{ref}' "
                        f"in sheet '{self.name}'"
                    )
            known.append(cc.name)
        for cur in self.currency_columns:
            if cur not in names:
                raise ValueError(f"currency column '{cur}' not found in sheet '{self.name}'")
        return self


class ChartSpec(_Strict):
    kind: Literal["bar", "line"]
    title: str = Field(min_length=1, max_length=100)
    sheet: str
    category_column: str
    value_columns: list[str] = Field(min_length=1, max_length=6)


class WorkbookDoc(_Strict):
    type: Literal["xlsx"] = "xlsx"
    title: str = Field(min_length=1, max_length=120)
    audience: str = "general business readers"
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    sheets: list[SheetSpec] = Field(min_length=1, max_length=10)
    charts: list[ChartSpec] = Field(default_factory=list, max_length=10)
    include_dashboard: bool = False

    @model_validator(mode="after")
    def _check(self) -> "WorkbookDoc":
        lowered = [s.name.lower() for s in self.sheets]
        if len(set(lowered)) != len(lowered):
            raise ValueError("sheet names must be unique (case-insensitive)")
        if self.include_dashboard and "dashboard" in lowered:
            raise ValueError("'Dashboard' is reserved when include_dashboard is true")
        by_name = {s.name: s for s in self.sheets}
        for ch in self.charts:
            sheet = by_name.get(ch.sheet)
            if sheet is None:
                raise ValueError(f"chart '{ch.title}' points at unknown sheet '{ch.sheet}'")
            cols = sheet.all_columns
            for ref in [ch.category_column, *ch.value_columns]:
                if ref not in cols:
                    raise ValueError(
                        f"chart '{ch.title}' uses unknown column '{ref}' in sheet '{ch.sheet}'"
                    )
        return self
