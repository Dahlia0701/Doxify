from __future__ import annotations

import re
import math
from typing import Annotated, Any, Literal, Optional, Union

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
Scalar = Union[str, int, float, bool, None]  # Union (not "|") so it also works on Python 3.9

_NUMBER_RE = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def is_number_text(text: str) -> bool:
    """True for text like '12', '-5', '3.5', '1e6'. Single definition of 'looks like a number'."""
    return bool(_NUMBER_RE.match(text.strip())) and not (re.match(r"^[+-]?0[0-9]+$", text.strip()) is not None) and math.isfinite(float(text.strip())) if _NUMBER_RE.match(text.strip()) else False


def _is_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(value) if isinstance(value, float) else True
    return isinstance(value, str) and is_number_text(value)


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

    def is_numeric(self, name: str, allow_empty: bool = False) -> bool:
        for cc in self.computed_columns:
            if cc.name == name:
                return all(self.is_numeric(c, allow_empty) for c in cc.columns)
        if name not in self.columns:
            return False
        idx = self.columns.index(name)
        values = [r[idx] for r in self.rows if r[idx] not in (None, "")]
        if not values:
            return allow_empty
        return all(_is_number(v) for v in values)

    @model_validator(mode="after")
    def _check(self) -> "SheetSpec":
        names = self.all_columns
        if any(not n.strip() for n in names):
            raise ValueError(f"sheet '{self.name}' has an empty column name")
        if len({n.lower() for n in names}) != len(names):
            raise ValueError(f"sheet '{self.name}' has duplicate column names")
        for i, row in enumerate(self.rows):
            if any(isinstance(v, float) and not math.isfinite(v) for v in row):
                raise ValueError(f"sheet '{self.name}' row {i+1} contains NaN or infinity")
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
                if not self.is_numeric(ref, allow_empty=True):
                    raise ValueError(
                        f"computed column '{cc.name}' needs numbers, but '{ref}' "
                        f"in sheet '{self.name}' contains text"
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
            if not sheet.rows:
                raise ValueError(f"chart '{ch.title}' requires at least one data row")
            cols = sheet.all_columns
            for ref in [ch.category_column, *ch.value_columns]:
                if ref not in cols:
                    raise ValueError(
                        f"chart '{ch.title}' uses unknown column '{ref}' in sheet '{ch.sheet}'"
                    )
            for ref in ch.value_columns:
                if not sheet.is_numeric(ref, allow_empty=True):
                    raise ValueError(
                        f"chart '{ch.title}' plots '{ref}', which contains text, not numbers"
                    )
        return self


MAX_SLIDES = 40
MAX_SECTIONS = 30
MAX_TABLE_ROWS = 200
SLIDE_TABLE_MAX_ROWS = 12
SLIDE_TABLE_MAX_COLS = 6


class SeriesData(_Strict):
    name: str = Field(min_length=1, max_length=60)
    values: list[float] = Field(min_length=1, max_length=60)

    @field_validator("values")
    @classmethod
    def _finite(cls, v: list[float]) -> list[float]:
        if any(not math.isfinite(x) for x in v):
            raise ValueError("chart values must be finite numbers")
        return v


class ChartData(_Strict):
    """Inline chart data used by slides and PDF sections."""

    kind: Literal["bar", "line", "pie"]
    title: str = Field(min_length=1, max_length=100)
    categories: list[str] = Field(min_length=1, max_length=60)
    series: list[SeriesData] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _check(self) -> "ChartData":
        for s in self.series:
            if len(s.values) != len(self.categories):
                raise ValueError(
                    f"chart '{self.title}': series '{s.name}' has {len(s.values)} values "
                    f"but there are {len(self.categories)} categories"
                )
        if self.kind == "pie":
            if len(self.series) != 1:
                raise ValueError(f"pie chart '{self.title}' needs exactly one series")
            if any(v < 0 for v in self.series[0].values):
                raise ValueError(f"pie chart '{self.title}' cannot have negative values")
        return self


class TableData(_Strict):
    columns: list[str] = Field(min_length=1, max_length=10)
    rows: list[list[Scalar]] = Field(min_length=1, max_length=MAX_TABLE_ROWS)
    caption: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _check(self) -> "TableData":
        for i, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(f"table row {i + 1} has {len(row)} values, expected {len(self.columns)}")
            if any(isinstance(v, float) and not math.isfinite(v) for v in row):
                raise ValueError(f"table row {i + 1} contains NaN or infinity")
        return self


class KpiItem(_Strict):
    label: str = Field(min_length=1, max_length=40)
    value: str = Field(min_length=1, max_length=20)
    note: str = Field(default="", max_length=80)


SlideLayout = Literal["title", "bullets", "two_column", "table", "chart", "kpis"]


class SlideSpec(_Strict):
    layout: SlideLayout
    title: str = Field(min_length=1, max_length=100)
    subtitle: str = Field(default="", max_length=160)
    bullets: list[str] = Field(default_factory=list, max_length=8)
    left_title: str = Field(default="", max_length=60)
    right_title: str = Field(default="", max_length=60)
    left: list[str] = Field(default_factory=list, max_length=6)
    right: list[str] = Field(default_factory=list, max_length=6)
    table: Optional[TableData] = None
    chart: Optional[ChartData] = None
    kpis: list[KpiItem] = Field(default_factory=list, max_length=4)
    notes: str = Field(default="", max_length=1500)

    @field_validator("bullets", "left", "right")
    @classmethod
    def _bullet_text(cls, v: list[str]) -> list[str]:
        for b in v:
            if not b.strip():
                raise ValueError("a bullet is empty")
            if len(b) > 260:
                raise ValueError(f"a bullet is {len(b)} characters; the limit is 260")
        return v

    @model_validator(mode="after")
    def _check(self) -> "SlideSpec":
        need = {
            "bullets": bool(self.bullets),
            "two_column": bool(self.left and self.right),
            "table": self.table is not None,
            "chart": self.chart is not None,
            "kpis": 2 <= len(self.kpis) <= 4,
        }
        if self.layout in need and not need[self.layout]:
            raise ValueError(f"slide '{self.title}': layout '{self.layout}' is missing its required content")
        if self.table is not None and (
            len(self.table.rows) > SLIDE_TABLE_MAX_ROWS or len(self.table.columns) > SLIDE_TABLE_MAX_COLS
        ):
            raise ValueError(
                f"slide '{self.title}': a slide table can have at most "
                f"{SLIDE_TABLE_MAX_ROWS} rows and {SLIDE_TABLE_MAX_COLS} columns"
            )
        return self


class DeckDoc(_Strict):
    type: Literal["pptx"] = "pptx"
    title: str = Field(min_length=1, max_length=120)
    audience: str = "business stakeholders"
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    slides: list[SlideSpec] = Field(min_length=1, max_length=MAX_SLIDES)


class ParagraphBlock(_Strict):
    kind: Literal["paragraph"] = "paragraph"
    text: str = Field(min_length=1, max_length=2500)


class BulletsBlock(_Strict):
    kind: Literal["bullets"] = "bullets"
    items: list[str] = Field(min_length=1, max_length=15)

    @field_validator("items")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        for b in v:
            if not b.strip() or len(b) > 400:
                raise ValueError("a bullet is empty or longer than 400 characters")
        return v


class TableBlock(_Strict):
    kind: Literal["table"] = "table"
    table: TableData


class ChartBlock(_Strict):
    kind: Literal["chart"] = "chart"
    chart: ChartData


Block = Annotated[Union[ParagraphBlock, BulletsBlock, TableBlock, ChartBlock], Field(discriminator="kind")]


class SectionSpec(_Strict):
    heading: str = Field(min_length=1, max_length=100)
    blocks: list[Block] = Field(min_length=1, max_length=20)


class PdfDoc(_Strict):
    type: Literal["pdf"] = "pdf"
    title: str = Field(min_length=1, max_length=120)
    subtitle: str = Field(default="", max_length=160)
    audience: str = "business readers"
    assumptions: list[str] = Field(default_factory=list, max_length=20)
    sections: list[SectionSpec] = Field(min_length=1, max_length=MAX_SECTIONS)
    include_toc: bool = False
    target_pages: Optional[int] = Field(default=None, ge=1, le=100)
    # Layout hints. The repair step adjusts these to reach target_pages.
    font_scale: float = Field(default=1.0, ge=0.8, le=1.2)
    section_breaks: bool = False

    @model_validator(mode="after")
    def _unique_headings(self) -> "PdfDoc":
        names = [s.heading.strip().lower() for s in self.sections]
        if len(set(names)) != len(names):
            raise ValueError("section headings must be unique")
        return self


AnyDoc = Union[WorkbookDoc, DeckDoc, PdfDoc]
DOC_MODELS: dict[str, type] = {"xlsx": WorkbookDoc, "pptx": DeckDoc, "pdf": PdfDoc}
DOC_FORMATS = ("pdf", "pptx", "xlsx")
