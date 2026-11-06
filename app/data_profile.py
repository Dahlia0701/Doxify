"""Design decision: when a CSV is supplied, every number in the output is calculated here with
pandas, never by the LLM as llm can generate misleading numbers. 
"""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from app.config import Settings
from app.intent import Intent
from app.limits import InputError
from app.schemas import (
    MAX_ROWS, ChartData, ChartSpec, DeckDoc, PdfDoc, SheetSpec, SeriesData, SlideSpec,
    TableData, WorkbookDoc, is_number_text,
)

MAX_COLUMNS = 50
TOP_CATEGORIES = 12
_BAD_SHEET = set('[]:*?/\\')
_CURRENCY = re.compile(r"[\$€£₹\s]")
_MONEY_NAME = re.compile(r"revenue|sales|amount|income|profit|spend|total|price|cost", re.I)
_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}([ T].*)?$|^\d{1,2}/\d{1,2}/\d{4}$")


@dataclass
class ColumnProfile:
    name: str
    kind: str  # "numeric" | "text" | "date"
    missing: int
    unique: int
    total: Optional[float] = None
    mean: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None


@dataclass
class Finding:
    metric_id: str
    value: float
    text: str


@dataclass
class DataProfile:
    df: pd.DataFrame  
    columns: list[ColumnProfile]
    category_column: Optional[str]
    date_column: Optional[str]
    value_columns: list[str]
    metrics: dict[str, float] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return len(self.df)

def _decode(data: bytes) -> str:
    if b"\x00" in data[:4096]:
        raise InputError("The uploaded file looks binary, not like a CSV. Export your data as a .csv text file.")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _unique_names(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(names, start=1):
        name = re.sub(r"\s+", " ", str(raw)).strip()[:60]
        if not name or name.lower().startswith("unnamed:"):
            name = f"Column {i}"
        base, n = name, 2
        while name.lower() in seen:
            name = f"{base[:55]} {n}"
            n += 1
        seen.add(name.lower())
        out.append(name)
    return out


def _numeric_series(col: pd.Series) -> Optional[pd.Series]:
    """Convert to numbers only if EVERY non-empty value parses. IDs like 00123 stay text."""
    vals = col[col != ""]
    if vals.empty:
        return None
    cleaned = vals.map(lambda v: _CURRENCY.sub("", v).replace(",", ""))
    if not cleaned.map(is_number_text).all():
        return None
    nums = pd.to_numeric(cleaned)
    if not nums.map(math.isfinite).all():
        return None
    out = pd.Series([None] * len(col), index=col.index, dtype="object")
    out.loc[vals.index] = nums
    if (nums % 1 == 0).all() and nums.abs().max() < 1e15:
        out = out.map(lambda x: None if x is None else int(x))
    else:
        out = out.map(lambda x: None if x is None else float(x))
    return out


def _date_series(col: pd.Series) -> Optional[pd.Series]:
    vals = col[col != ""]
    if vals.empty or not vals.map(lambda v: bool(_DATE_RE.match(v.strip()))).all():
        return None
    parsed = pd.to_datetime(vals, errors="coerce", dayfirst=False, format="mixed")
    if parsed.isna().any():
        return None
    out = pd.Series([None] * len(col), index=col.index, dtype="object")
    out.loc[vals.index] = parsed.dt.strftime("%Y-%m-%d")
    return out


def load_csv(data: bytes, settings: Settings) -> pd.DataFrame:
    """Parse CSV bytes into a typed DataFrame. Raises InputError with an actionable message."""
    text = _decode(data)
    if not text.strip():
        raise InputError("The CSV file contains no data. Upload a file with a header row and at least one data row.")
    first = text.splitlines()[0]
    sep = max([",", ";", "\t", "|"], key=first.count) if first.count(",") == 0 else ","
    try:
        raw = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False,
                          on_bad_lines="error", skip_blank_lines=True)
    except pd.errors.EmptyDataError as exc:
        raise InputError("The CSV file contains no data. Upload a file with a header row and at least one data row.") from exc
    except pd.errors.ParserError as exc:
        detail = str(exc).splitlines()[0][:140]
        raise InputError(
            f"The CSV is malformed ({detail}). Check that every row has the same number of columns "
            "and that text containing commas is quoted."
        ) from exc
    except Exception as exc:  # defensive: never leak internals
        raise InputError(f"The CSV could not be read ({type(exc).__name__}). Re-export it as UTF-8 CSV.") from exc

    raw = raw.map(lambda v: v.strip() if isinstance(v, str) else v)
    raw = raw.loc[:, ~(raw == "").all()]  # drop fully empty columns
    raw = raw.loc[~(raw == "").all(axis=1)].reset_index(drop=True)
    if raw.shape[1] == 0 or raw.shape[0] == 0:
        raise InputError("The CSV has a header but no usable data rows. Add data rows and retry.")
    if raw.shape[1] > MAX_COLUMNS:
        raise InputError(f"The CSV has {raw.shape[1]} columns; the limit is {MAX_COLUMNS}. Remove columns and retry.")
    if raw.shape[0] > MAX_ROWS:
        raise InputError(f"The CSV has {raw.shape[0]} rows; the limit is {MAX_ROWS}. Aggregate or split it and retry.")
    raw.columns = _unique_names(list(raw.columns))

    typed = pd.DataFrame(index=raw.index)
    for name in raw.columns:
        col = raw[name]
        converted = _numeric_series(col)
        if converted is None:
            converted = _date_series(col)
        if converted is None:
            converted = col.map(lambda v: None if v == "" else v)
        typed[name] = converted.astype(object)
    return typed


def _kind(df: pd.DataFrame, name: str) -> str:
    vals = [v for v in df[name] if v is not None]
    if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
        return "numeric"
    if vals and all(isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v) for v in vals) and _date_series(df[name].map(lambda v: "" if v is None else v)) is not None:
        return "date"
    return "text"


def _fmt(x: float) -> str:
    if abs(x) >= 1000 or float(x).is_integer():
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def profile_dataframe(df: pd.DataFrame) -> DataProfile:
    cols: list[ColumnProfile] = []
    kinds: dict[str, str] = {}
    for name in df.columns:
        kind = _kind(df, name)
        kinds[name] = kind
        s = df[name]
        cp = ColumnProfile(name, kind, int(s.isna().sum() + (s == "").sum()), int(s.dropna().nunique()))
        if kind == "numeric":
            nums = pd.to_numeric(s.dropna())
            if len(nums):
                cp.total, cp.mean = float(nums.sum()), float(nums.mean())
                cp.minimum, cp.maximum = float(nums.min()), float(nums.max())
        cols.append(cp)

    n = len(df)
    numeric = [c.name for c in cols if c.kind == "numeric"]
    # "value" columns: numeric and not an ID-like column (all unique integers)
    value_cols = [c for c in numeric if not (df[c].dropna().nunique() == n and n > 20 and all(isinstance(v, int) for v in df[c].dropna()) and re.search(r"\bid\b|_id$|^id", c.lower()))]
    dates = [c.name for c in cols if c.kind == "date"]
    cats = [c.name for c in cols if c.kind == "text" and 1 < c.unique <= max(TOP_CATEGORIES * 3, n // 2) and c.unique < n]
    value_cols.sort(key=lambda c: 0 if _MONEY_NAME.search(c) else 1)  # stable: money-like names first
    prof = DataProfile(df=df, columns=cols, category_column=cats[0] if cats else None,
                       date_column=dates[0] if dates else None, value_columns=value_cols[:6])
    _compute_findings(prof)
    return prof


def summary_by_category(prof: DataProfile, limit: int = TOP_CATEGORIES, max_values: int = 3) -> Optional[pd.DataFrame]:
    if not prof.category_column or not prof.value_columns:
        return None
    vals = prof.value_columns[:max_values]
    work = prof.df[[prof.category_column] + vals].dropna(subset=[prof.category_column]).copy()
    for v in vals:
        work[v] = pd.to_numeric(work[v])
    g = work.groupby(prof.category_column, sort=False)[vals].sum()
    return g.sort_values(vals[0], ascending=False).head(limit).reset_index()


def monthly_trend(prof: DataProfile) -> Optional[pd.DataFrame]:
    if not prof.date_column or not prof.value_columns:
        return None
    v = prof.value_columns[0]
    work = prof.df[[prof.date_column, v]].dropna().copy()
    if work.empty:
        return None
    work["Month"] = work[prof.date_column].str.slice(0, 7)
    g = work.groupby("Month")[v].apply(lambda s: float(pd.to_numeric(s).sum())).reset_index()
    return g.sort_values("Month") if len(g) >= 2 else None


def _compute_findings(prof: DataProfile) -> None:
    f, m = prof.findings, prof.metrics
    df = prof.df
    f.append(Finding("rows", float(len(df)), f"The dataset has {len(df):,} rows and {df.shape[1]} columns."))
    m["rows"] = float(len(df))
    if prof.value_columns:
        main = prof.value_columns[0]
        cp = next(c for c in prof.columns if c.name == main)
        if cp.total is not None:
            m[f"total:{main}"], m[f"mean:{main}"] = cp.total, cp.mean or 0.0
            f.append(Finding(f"total:{main}", cp.total, f"Total {main} is {_fmt(cp.total)}."))
            f.append(Finding(f"mean:{main}", cp.mean or 0.0, f"Average {main} per row is {_fmt(cp.mean or 0.0)}."))
    summ = summary_by_category(prof, limit=10_000, max_values=1)
    if summ is not None and len(summ) >= 2:
        main, cat = prof.value_columns[0], prof.category_column
        total = float(summ[main].sum())
        if total > 0:
            top_name, top_val = summ.iloc[0][cat], float(summ.iloc[0][main])
            share = round(100 * top_val / total, 1)
            m[f"top_share:{main}"] = share
            f.append(Finding(f"top_share:{main}", share, f"{top_name} is the largest {cat} for {main}, with {share}% of the total."))
            low_name, low_val = summ.iloc[-1][cat], float(summ.iloc[-1][main])
            m[f"bottom:{main}"] = low_val
            f.append(Finding(f"bottom:{main}", low_val, f"{low_name} is the smallest {cat} for {main}, at {_fmt(low_val)}."))
    trend = monthly_trend(prof)
    if trend is not None:
        main = prof.value_columns[0]
        first, last = float(trend.iloc[0][main]), float(trend.iloc[-1][main])
        best = trend.loc[trend[main].idxmax()]
        m[f"peak:{main}"] = float(best[main])
        f.append(Finding(f"peak:{main}", float(best[main]), f"{best['Month']} was the strongest month for {main}, at {_fmt(float(best[main]))}."))
        if first != 0:
            chg = round(100 * (last - first) / abs(first), 1)
            m[f"change:{main}"] = chg
            f.append(Finding(f"change:{main}", chg, f"{main} moved {chg:+.1f}% from {trend.iloc[0]['Month']} to {trend.iloc[-1]['Month']}."))
    missing = sum(c.missing for c in prof.columns)
    if missing:
        m["missing_cells"] = float(missing)
        prof.notes.append(f"{missing:,} empty cells were found; they were left blank and ignored in totals.")
        f.append(Finding("missing_cells", float(missing), f"{missing:,} cells are empty across the dataset."))

def _py(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float):
        v = round(v, 6)  
    return v


def _sheet_name(base: str, taken: set[str]) -> str:
    name = "".join(ch for ch in base if ch not in _BAD_SHEET).strip().strip("'")[:31] or "Sheet"
    cand, n = name, 2
    while cand.lower() in taken:
        cand = f"{name[:28]} {n}"
        n += 1
    taken.add(cand.lower())
    return cand


def _title_from(intent: Intent, prof: DataProfile) -> str:
    topic = intent.topic if intent.topic and len(intent.topic.split()) <= 8 else ""
    topic = re.sub(r"\b(?:this|sample)\b|\bdataset\b|\bdata\b", "", topic, flags=re.I).strip()
    topic = re.sub(r"\s+", " ", topic).strip()
    return (f"{topic.title()} Analysis" if topic else "Data Analysis Report")[:100]

def build_workbook(prof: DataProfile, intent: Intent) -> WorkbookDoc:
    taken: set[str] = {"dashboard"}
    data_name = _sheet_name("Data", taken)
    sheets: list[SheetSpec] = [
        SheetSpec(name=data_name, columns=list(prof.df.columns),
                  rows=[[_py(v) for v in row] for row in prof.df.itertuples(index=False, name=None)])
    ]
    charts: list[ChartSpec] = []
    summ = summary_by_category(prof)
    if summ is not None:
        nm = _sheet_name(f"By {prof.category_column}", taken)
        cols = list(summ.columns)
        sheets.append(SheetSpec(name=nm, columns=cols, rows=[[_py(v) for v in r] for r in summ.itertuples(index=False, name=None)],
                                totals_row=True))
        charts.append(ChartSpec(kind="bar", title=f"{prof.value_columns[0]} by {prof.category_column}", sheet=nm,
                                category_column=cols[0], value_columns=cols[1:2]))
    trend = monthly_trend(prof)
    if trend is not None:
        nm = _sheet_name("Monthly Trend", taken)
        sheets.append(SheetSpec(name=nm, columns=["Month", prof.value_columns[0]],
                                rows=[[r[0], _py(r[1])] for r in trend.itertuples(index=False, name=None)]))
        charts.append(ChartSpec(kind="line", title=f"{prof.value_columns[0]} by month", sheet=nm,
                                category_column="Month", value_columns=[prof.value_columns[0]]))
    findings_name = _sheet_name("Key Findings", taken)
    sheets.append(SheetSpec(name=findings_name, columns=["Finding"], rows=[[x.text] for x in prof.findings]))
    assumptions = list(intent.assumptions) + prof.notes
    if not charts:
        assumptions.append("No category or date column suitable for charts was found, so no charts were added.")
    return WorkbookDoc(title=_title_from(intent, prof), audience=intent.audience, assumptions=assumptions,
                       sheets=sheets, charts=charts, include_dashboard=bool(charts))


def _chart_from(summ: pd.DataFrame, title: str, kind: str = "bar", n: int = 8) -> ChartData:
    s = summ.head(n)
    return ChartData(kind=kind, title=title, categories=[str(c) for c in s.iloc[:, 0]],
                     series=[SeriesData(name=str(summ.columns[1]), values=[float(v) for v in s.iloc[:, 1]])])


def _trend_chart(trend: pd.DataFrame, name: str) -> ChartData:
    return ChartData(kind="line", title=f"{name} by month", categories=list(trend["Month"]),
                     series=[SeriesData(name=name, values=[float(v) for v in trend[name]])])


def _count_chart(prof: DataProfile, col: str) -> Optional[ChartData]:
    counts = prof.df[col].dropna().value_counts().head(8)
    if len(counts) < 2:
        return None
    return ChartData(kind="bar", title=f"Rows by {col}", categories=[str(c) for c in counts.index],
                     series=[SeriesData(name="Rows", values=[float(v) for v in counts.values])])


def build_deck(prof: DataProfile, intent: Intent) -> DeckDoc:
    """Slides carry a priority so an explicit slide count can be met by dropping the least
    important slides, or by adding extra breakdown slides computed from the same data."""
    title = _title_from(intent, prof)
    main = prof.value_columns[0] if prof.value_columns else None
    ranked: list[tuple[int, SlideSpec]] = [(100, SlideSpec(layout="title", title=title, subtitle=f"Prepared for {intent.audience}"))]
    kpis = []
    for c in prof.columns:
        if c.name in prof.value_columns[:3] and c.total is not None:
            kpis.append({"label": f"Total {c.name}"[:40], "value": _fmt(c.total)[:20], "note": f"Average {_fmt(c.mean or 0)}"})
    kpis.insert(0, {"label": "Rows analysed", "value": f"{prof.rows:,}", "note": f"{len(prof.columns)} columns"})
    ranked.append((80, SlideSpec(layout="kpis", title="Dataset at a glance", kpis=kpis[:4])))
    summ = summary_by_category(prof, limit=8, max_values=2)
    if summ is not None:
        ranked.append((70, SlideSpec(layout="chart", title=f"{main} by {prof.category_column}"[:100],
                                     chart=_chart_from(summ, f"{main} by {prof.category_column}"),
                                     bullets=[x.text for x in prof.findings if x.metric_id.startswith("top_share")][:1])))
        rows = [[_py(v) for v in r] for r in summ.itertuples(index=False, name=None)]
        ranked.append((50, SlideSpec(layout="table", title=f"Top {prof.category_column} values"[:100],
                                     table=TableData(columns=[str(c) for c in summ.columns], rows=rows, caption="Computed from the uploaded data"))))
    trend = monthly_trend(prof)
    if trend is not None:
        ranked.append((60, SlideSpec(layout="chart", title=f"{main} over time"[:100], chart=_trend_chart(trend, main))))
    ranked.append((90, SlideSpec(layout="bullets", title="Key findings",
                                 bullets=[x.text for x in prof.findings if x.metric_id != "rows"][:6] or [prof.findings[0].text])))
    if prof.notes:
        ranked.append((30, SlideSpec(layout="bullets", title="Data notes", bullets=prof.notes[:6])))

    extras: list[SlideSpec] = []
    for col in prof.value_columns[1:]:
        s2 = summary_by_category(DataProfile(prof.df, prof.columns, prof.category_column, prof.date_column, [col]), limit=8, max_values=1)
        if s2 is not None:
            extras.append(SlideSpec(layout="chart", title=f"{col} by {prof.category_column}"[:100], chart=_chart_from(s2, f"{col} by {prof.category_column}")))
    for c in prof.columns:
        if c.kind == "text" and c.name != prof.category_column and 2 <= c.unique <= 12:
            ch = _count_chart(prof, c.name)
            if ch is not None:
                extras.append(SlideSpec(layout="chart", title=f"Rows by {c.name}"[:100], chart=ch))

    notes = list(intent.assumptions)
    n = intent.slide_count if intent.slide_count_explicit else None
    if n:
        while len(ranked) < n and extras:
            ranked.insert(-1, (40, extras.pop(0)))  
        while len(ranked) > n and len(ranked) > 1:
            drop = min(range(1, len(ranked)), key=lambda k: ranked[k][0]) 
            ranked.pop(drop)
        if len(ranked) < n:
            notes.append(f"Only {len(ranked)} slides' worth of content could be derived from the data; {n} were requested.")
    return DeckDoc(title=title, audience=intent.audience, assumptions=notes, slides=[s for _, s in ranked])


def build_pdf(prof: DataProfile, intent: Intent) -> PdfDoc:
    title = _title_from(intent, prof)
    main = prof.value_columns[0] if prof.value_columns else None
    from app.schemas import BulletsBlock, ChartBlock, ParagraphBlock, SectionSpec, TableBlock

    secs: list[SectionSpec] = [SectionSpec(heading="Executive Summary", blocks=[
        ParagraphBlock(text=f"This report analyses a dataset of {prof.rows:,} rows and {len(prof.columns)} columns prepared for {intent.audience}."),
        BulletsBlock(items=[x.text for x in prof.findings if x.metric_id != "rows"][:6] or [prof.findings[0].text])])]
    prof_rows = [[c.name, c.kind, c.unique, c.missing, "" if c.total is None else _fmt(c.total)] for c in prof.columns][:40]
    secs.append(SectionSpec(heading="Data Overview", blocks=[TableBlock(table=TableData(
        columns=["Column", "Type", "Unique values", "Missing", "Total"], rows=prof_rows, caption="Column profile"))]))
    summ = summary_by_category(prof, limit=12, max_values=3)
    if summ is not None:
        rows = [[_py(v) if not isinstance(_py(v), float) else round(_py(v), 2) for v in r] for r in summ.itertuples(index=False, name=None)]
        secs.append(SectionSpec(heading=f"Analysis by {prof.category_column}"[:100], blocks=[
            ChartBlock(chart=_chart_from(summ, f"{main} by {prof.category_column}")),
            TableBlock(table=TableData(columns=[str(c) for c in summ.columns], rows=rows, caption="Totals per category"))]))
    trend = monthly_trend(prof)
    if trend is not None:
        secs.append(SectionSpec(heading="Trend Over Time", blocks=[ChartBlock(chart=_trend_chart(trend, main))]))
    if prof.notes:
        secs.append(SectionSpec(heading="Data Quality Notes", blocks=[BulletsBlock(items=prof.notes[:10])]))
    return PdfDoc(title=title, subtitle=f"Prepared for {intent.audience}", audience=intent.audience,
                  assumptions=list(intent.assumptions), sections=secs, include_toc=len(secs) >= 5,
                  target_pages=intent.page_count)


def build_from_profile(prof: DataProfile, intent: Intent):
    if intent.fmt == "xlsx":
        return build_workbook(prof, intent)
    if intent.fmt == "pptx":
        return build_deck(prof, intent)
    return build_pdf(prof, intent)


def claims_for(prof: DataProfile) -> list[dict]:
    """Structured claims for validators.content_validator.validate_metric_claims."""
    return [{"metric_id": f.metric_id, "value": f.value, "text": f.text} for f in prof.findings]
