"""it understands the prompt and extracts the file type , audience, counts mentioned 
and Anything the prompt does not say gets a default AND an entry in `assumptions`,
so the user can see what was guessed.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from app.limits import InputError
from app.schemas import DOC_FORMATS, MAX_SLIDES

MAX_PAGES = 100
DEFAULT_SLIDES = 8

_FORMAT_WORDS: dict[str, list[str]] = {
    "pptx": [r"powerpoint", r"pptx?", r"slides?", r"slide\s*deck", r"presentation", r"deck"],
    "xlsx": [r"excel", r"xlsx", r"spreadsheet", r"workbook"],
    "pdf": [r"pdf"],
}
_WEAK_PDF = [r"proposal", r"report", r"memo", r"whitepaper", r"white paper", r"document", r"brief", r"one-pager"]

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20,
}
_NUM = r"(\d{1,4}|" + "|".join(_NUMBER_WORDS) + r")"

_AUDIENCES = [
    "senior leadership", "executives", "executive team", "board members", "the board", "investors",
    "customers", "clients", "students", "engineers", "managers", "finance team", "sales team",
    "marketing team", "stakeholders", "beginners", "leadership",
]
_BAD_AUDIENCE_WORDS = {"me", "you", "it", "us", "him", "her", "them", "this", "that", "my", "myself"}

DEFAULT_AUDIENCE = {
    "pptx": "business stakeholders",
    "pdf": "business readers",
    "xlsx": "general business readers",
}


@dataclass
class Intent:
    fmt: str
    fmt_source: str  # "selected" | "prompt" | "csv" | "default"
    audience: str
    topic: str
    slide_count: Optional[int] = None
    slide_count_explicit: bool = False  # False when the count is only our default
    page_count: Optional[int] = None
    sections: list[str] = field(default_factory=list)
    wants_dashboard: bool = False
    wants_charts: bool = False
    wants_formulas: bool = False
    has_data: bool = False
    asks_for_data_analysis: bool = False
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _to_int(token: str) -> int:
    return int(token) if token.isdigit() else _NUMBER_WORDS[token.lower()]


def _find_formats(text: str) -> list[tuple[int, str]]:
    """(position, format) for each explicit format word, in order of appearance."""
    hits: list[tuple[int, str]] = []
    for fmt, words in _FORMAT_WORDS.items():
        for w in words:
            m = re.search(rf"\b{w}\b", text)
            if m:
                hits.append((m.start(), fmt))
    return sorted(hits)


def detect_format(prompt: str, selected: Optional[str], has_csv: bool) -> tuple[str, str, list[str]]:
    """Returns (format, source, notes)."""
    text = prompt.lower()
    notes: list[str] = []
    found = _find_formats(text)
    mentioned = list(dict.fromkeys(f for _, f in found))
    if selected and selected != "auto":
        if selected not in DOC_FORMATS:
            raise InputError(f"Unknown format '{selected}'. Choose one of: pdf, pptx, xlsx (or auto).")
        others = [f for f in mentioned if f != selected]
        if others:
            notes.append(
                f"The format selected in the form ({selected}) was used instead of the "
                f"'{others[0]}' mentioned in the prompt."
            )
        return selected, "selected", notes
    if mentioned:
        if len(mentioned) > 1:
            notes.append(f"The prompt mentions several formats; the first one ({mentioned[0]}) was used.")
        return mentioned[0], "prompt", notes
    if any(re.search(rf"\b{w}\b", text) for w in _WEAK_PDF):
        return "pdf", "prompt", ["No file type was named; a written document was assumed, so a PDF was produced."]
    if has_csv:
        return "xlsx", "csv", ["A CSV was uploaded and no format was named, so an Excel report was produced."]
    return "pdf", "default", ["No output format was named, so a PDF was produced."]


def _detect_audience(prompt: str, fmt: str) -> tuple[str, bool]:
    low = prompt.lower()
    for aud in _AUDIENCES:
        if re.search(rf"\bfor\s+(?:our\s+|the\s+|an?\s+)?{re.escape(aud)}\b", low) or re.search(rf"\bto\s+{re.escape(aud)}\b", low):
            return aud.replace("the board", "board members"), True
    m = re.search(r"\bfor\s+(?:our\s+|the\s+|an?\s+)?([a-z][a-z\- ]{2,40}?)(?=\s*[.,;:]|\s+(?:with|including|that|about|covering|containing|and|as|in)\b|$)", low)
    if m:
        cand = m.group(1).strip()
        words = cand.split()
        if len(words) <= 4 and not (set(words) & _BAD_AUDIENCE_WORDS):
            return cand, True
    return DEFAULT_AUDIENCE[fmt], False


def _detect_counts(prompt: str, fmt: str) -> tuple[Optional[int], Optional[int]]:
    low = prompt.lower()
    slides = pages = None
    m = re.search(rf"\b{_NUM}[\s-]*slides?\b", low) or re.search(r"\bslides?\s*[:=]\s*(\d{1,4})\b", low)
    if m:
        slides = _to_int(m.group(1))
    m = re.search(rf"\b{_NUM}[\s-]*pages?\b", low) or re.search(r"\bpages?\s*[:=]\s*(\d{1,4})\b", low)
    if m:
        pages = _to_int(m.group(1))
    if slides is not None and not 1 <= slides <= MAX_SLIDES:
        raise InputError(f"{slides} slides requested; a deck can have 1 to {MAX_SLIDES} slides.")
    if pages is not None and not 1 <= pages <= MAX_PAGES:
        raise InputError(f"{pages} pages requested; a PDF can have 1 to {MAX_PAGES} pages.")
    return (slides if fmt == "pptx" else None), (pages if fmt == "pdf" else None)


def _clean_section(raw: str) -> str:
    s = re.sub(r"^(?:and|or)\s+", "", raw.strip(), flags=re.I)
    s = re.sub(r"^(?:an?|the|our|your|some)\s+", "", s, flags=re.I)
    s = s.strip(" .;:")
    return " ".join(w.capitalize() if w.islower() else w for w in s.split())


def _detect_sections(prompt: str) -> list[str]:
    m = re.search(
        r"\b(?:including|include|includes|containing|covering|with sections?(?: for| on)?|sections?\s*:)\s+(.+?)(?:[.!?]\s|[.!?]$|$)",
        prompt, flags=re.I | re.S,
    )
    if not m:
        return []
    parts = re.split(r",|;|\band\b|\bplus\b", m.group(1), flags=re.I)
    out: list[str] = []
    for p in parts:
        name = _clean_section(p)
        if name and len(name.split()) <= 5 and name.lower() not in {s.lower() for s in out}:
            out.append(name)
    return out[:12]


def _detect_topic(prompt: str) -> str:
    m = re.search(
        r"\b(?:explaining|about|on|covering|summari[sz]ing|analy[sz]ing|presenting|describing)\s+(.+?)(?=\s+(?:for|with|including|containing|as|in|using|and)\b|[.,;:]|$)",
        prompt, flags=re.I,
    )
    topic = m.group(1).strip() if m else ""
    if not topic:
        m = re.search(r"\b(?:project|business|sales|financial|marketing|product|annual|quarterly|monthly)\s+[a-z ]{3,40}?(?=\s+(?:as|for|with|including|in|and)\b|[.,;:]|$)", prompt, flags=re.I)
        topic = m.group(0).strip() if m else ""
    if not topic:
        topic = " ".join(prompt.split()[:8])
    topic = re.sub(r"^(?:our|the|an?)\s+", "", topic, flags=re.I)
    return topic[:80].strip(" .")


def parse_intent(prompt: str, selected_format: Optional[str] = None, has_csv: bool = False) -> Intent:
    """Understand the prompt. Raises InputError for requests that cannot be satisfied."""
    words = re.findall(r"[A-Za-z]{2,}", prompt)
    if len(words) < 3:
        raise InputError(
            "The prompt is too vague to act on. Say what document you want, what it covers and who it is for, "
            "for example: 'Create a 10-slide deck about Q3 sales for executives.'"
        )
    fmt, source, notes = detect_format(prompt, selected_format, has_csv)
    audience, found_audience = _detect_audience(prompt, fmt)
    slides, pages = _detect_counts(prompt, fmt)
    low = prompt.lower()
    sections = _detect_sections(prompt) if fmt == "pdf" else []
    asks_data = bool(re.search(r"\b(analy[sz]e|dataset|data set|csv|attached|this data|sample data)\b", low))

    assumptions = list(notes)
    if not found_audience:
        assumptions.append(f"No audience was stated, so '{audience}' was assumed.")
    slides_explicit = slides is not None
    if fmt == "pptx" and slides is None:
        slides = DEFAULT_SLIDES
        assumptions.append(f"No slide count was stated, so {DEFAULT_SLIDES} slides were planned.")
    if fmt == "pdf" and not sections:
        assumptions.append("No sections were listed, so a standard structure for this kind of document was chosen.")
    if asks_data and not has_csv:
        assumptions.append("The prompt refers to data but no CSV was uploaded, so clearly labelled illustrative figures were used.")
    elif not has_csv and fmt in ("xlsx", "pptx") and re.search(r"\b(revenue|expenses?|profit|sales|budget|performance|kpi)\b", low):
        assumptions.append("No data was supplied, so clearly labelled illustrative figures were used.")

    return Intent(
        fmt=fmt,
        fmt_source=source,
        audience=audience,
        topic=_detect_topic(prompt),
        slide_count=slides,
        slide_count_explicit=slides_explicit,
        page_count=pages,
        sections=sections,
        wants_dashboard=bool(re.search(r"\bdashboard\b", low)),
        wants_charts=bool(re.search(r"\b(charts?|graphs?|plots?|visuali[sz]ations?)\b", low)),
        wants_formulas=bool(re.search(r"\bformulas?\b", low)),
        has_data=has_csv,
        asks_for_data_analysis=asks_data,
        assumptions=assumptions,
    )
