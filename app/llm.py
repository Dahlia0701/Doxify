"""llm module: defines the LLM interface and implemenatations for gemini and mock llm. 
"""
from __future__ import annotations

import json
import time
from typing import Callable, Protocol

from app.config import Settings

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"  


class LLMError(Exception):
    """Raised when the LLM call fails or no provider is configured. Message is user-facing."""


class LLM(Protocol):
    def complete_json(self, system: str, user: str) -> str: ...


MOCK_XLSX_PLAN = {
    "type": "xlsx",
    "title": "Monthly Financial Summary",
    "audience": "finance team",
    "assumptions": ["No data supplied, so sample monthly figures were used."],
    "sheets": [
        {
            "name": "Monthly Data",
            "columns": ["Month", "Revenue", "Expenses"],
            "rows": [
                ["Jan", 120000, 80000],
                ["Feb", 135000, 85000],
                ["Mar", 128000, 90000],
                ["Apr", 150000, 95000],
                ["May", 160000, 99000],
                ["Jun", 170000, 105000],
            ],
            "computed_columns": [
                {"name": "Profit", "formula_type": "difference", "columns": ["Revenue", "Expenses"], "number_format": "currency"},
                {"name": "Margin", "formula_type": "ratio", "columns": ["Profit", "Revenue"], "number_format": "percent"},
            ],
            "totals_row": True,
            "currency_columns": ["Revenue", "Expenses"],
        }
    ],
    "charts": [
        {"kind": "bar", "title": "Revenue vs Expenses", "sheet": "Monthly Data", "category_column": "Month", "value_columns": ["Revenue", "Expenses"]},
        {"kind": "line", "title": "Profit Trend", "sheet": "Monthly Data", "category_column": "Month", "value_columns": ["Profit"]},
    ],
    "include_dashboard": True,
}


def _mock_deck(n: int, title: str, audience: str) -> dict:
    """A canned but complete deck with exactly n slides (n >= 1)."""
    body = [
        {"layout": "kpis", "title": "Headline results", "kpis": [
            {"label": "Revenue", "value": "$4.2M", "note": "+8% vs prior quarter"},
            {"label": "Gross margin", "value": "62%", "note": "+1.5 pts"},
            {"label": "Net new customers", "value": "318", "note": "+12%"}]},
        {"layout": "chart", "title": "Revenue by month", "bullets": ["Growth was steady through the period"],
         "chart": {"kind": "bar", "title": "Revenue ($K)", "categories": ["Jul", "Aug", "Sep"],
                   "series": [{"name": "Revenue", "values": [1320, 1390, 1490]}]}},
        {"layout": "bullets", "title": "What drove performance", "bullets": [
            "Enterprise renewals exceeded plan", "Mid-market pipeline converted faster",
            "Support costs fell after tooling upgrades"]},
        {"layout": "two_column", "title": "Strengths and risks", "left_title": "Strengths", "right_title": "Risks",
         "left": ["Strong retention", "Healthy margins"], "right": ["Concentration in two accounts", "Hiring lag in sales"]},
        {"layout": "table", "title": "Regional summary", "table": {
            "columns": ["Region", "Revenue ($K)", "Growth"],
            "rows": [["North", 1500, "9%"], ["South", 1100, "6%"], ["West", 1600, "10%"]], "caption": "Illustrative figures"}},
        {"layout": "chart", "title": "Margin trend", "chart": {"kind": "line", "title": "Gross margin (%)",
            "categories": ["Q1", "Q2", "Q3"], "series": [{"name": "Margin", "values": [59.5, 60.8, 62.0]}]}},
        {"layout": "bullets", "title": "Priorities for next quarter", "bullets": [
            "Reduce account concentration", "Accelerate sales hiring", "Expand self-serve onboarding"]},
        {"layout": "bullets", "title": "Decisions requested", "bullets": [
            "Approve two sales hires", "Endorse the onboarding investment"]},
    ]
    slides = [{"layout": "title", "title": title, "subtitle": f"Prepared for {audience}"}]
    i = 0
    while len(slides) < n:
        base = dict(body[i % len(body)])
        if i >= len(body):
            base["title"] = f"{base['title']} (continued {i // len(body) + 1})"
        slides.append(base)
        i += 1
    return {"type": "pptx", "title": title, "audience": audience,
            "assumptions": ["All figures are illustrative and were not supplied by the user."],
            "slides": slides[:n]}


def _mock_pdf(sections: list[str], title: str, audience: str, target_pages: int | None) -> dict:
    names = sections or ["Executive Summary", "Scope and Approach", "Timeline", "Budget", "Risks", "Next Steps"]
    tables = {
        "timeline": {"columns": ["Phase", "Start", "End", "Owner"], "rows": [
            ["Discovery", "Week 1", "Week 3", "Project lead"], ["Build", "Week 4", "Week 10", "Engineering"],
            ["Launch", "Week 11", "Week 12", "Operations"]], "caption": "Planned phases"},
        "budget": {"columns": ["Item", "Cost (USD)"], "rows": [
            ["People", 120000], ["Tools and licences", 18000], ["Contingency", 15000]], "caption": "Illustrative budget"},
        "risk": {"columns": ["Risk", "Likelihood", "Impact", "Mitigation"], "rows": [
            ["Scope creep", "Medium", "High", "Change control board"], ["Key person leaves", "Low", "High", "Cross-training"]],
         "caption": "Top risks"},
    }
    out = []
    for name in names:
        low = name.lower()
        blocks: list[dict] = [{"kind": "paragraph", "text": (
            f"This section covers {low} for {title}. It is written for {audience} and states the main points, "
            "the reasoning behind them and what is needed from the reader.")}]
        key = next((k for k in tables if k in low), None)
        if key:
            blocks.append({"kind": "table", "table": tables[key]})
        if "budget" in low:
            blocks.append({"kind": "chart", "chart": {"kind": "pie", "title": "Budget split", "categories": ["People", "Tools", "Contingency"],
                                                      "series": [{"name": "Cost", "values": [120000, 18000, 15000]}]}})
        else:
            blocks.append({"kind": "bullets", "items": [f"Key point one about {low}", f"Key point two about {low}", f"Key point three about {low}"]})
        out.append({"heading": name, "blocks": blocks})
    return {"type": "pdf", "title": title, "subtitle": "Summary and recommendations", "audience": audience,
            "assumptions": ["Figures and dates are illustrative and were not supplied by the user."],
            "include_toc": len(out) >= 5, "sections": out}


def _extract_json_payload(text: str) -> dict:
    """Extract the JSON payload following '(JSON):' from a prompt, even with nested objects."""
    if not text:
        return {}
    marker = text.find("(JSON):")
    if marker == -1:
        return {}
    start = text.find("{", marker)
    if start == -1:
        return {}
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    break
    return {}


def _extract_response_text(response: object) -> str | None:
    """Support the SDK's common response shapes without assuming a single field layout."""
    if response is None:
        return None
    if isinstance(response, str):
        return response

    def _collect_parts(parts: object) -> str | None:
        if not isinstance(parts, list):
            return None
        texts: list[str] = []
        for part in parts:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str):
                texts.append(part_text)
        return "".join(texts) if texts else None

    for attr in ("text", "content", "output_text"):
        value = getattr(response, attr, None)
        if isinstance(value, str):
            return value
    for attr in ("parsed",):
        value = getattr(response, attr, None)
        if isinstance(value, dict):
            return json.dumps(value)

    parts = getattr(response, "parts", None)
    text = _collect_parts(parts)
    if text:
        return text

    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            if content is None:
                continue
            text = _collect_parts(getattr(content, "parts", None))
            if text:
                return text
    return None


class MockLLM:
    """Returns canned JSON. Used for development and tests so nothing costs money.

    - canned=<text>: always returns that text (tests use this to simulate bad output).
    - canned=None: reads 'FORMAT: x' from the system prompt and the requirements JSON from
      the user prompt, so the mock honours slide counts and requested sections.
    """

    def __init__(self, canned: str | None = None, queue: list[str] | None = None):
        self.canned = canned
        self.queue = list(queue or [])  # responses returned one by one, then fall back
        self.calls = 0
        self.last_user = ""

    def complete_json(self, system: str, user: str) -> str:
        import re

        self.calls += 1
        self.last_user = user
        if self.queue:
            return self.queue.pop(0)
        if self.canned is not None:
            return self.canned
        m = re.search(r"FORMAT: (\w+)", system)
        fmt = m.group(1) if m else "xlsx"
        req: dict = _extract_json_payload(user)
        topic_value = req.get("topic")
        topic = str(topic_value).strip() if topic_value is not None else "Business Overview"
        title = " ".join(w.capitalize() if w.islower() else w for w in topic.split())[:100] or "Business Overview"
        audience_value = req.get("audience")
        audience = str(audience_value).strip() if audience_value is not None else "business readers"
        if fmt == "pptx":
            return json.dumps(_mock_deck(int(req.get("slide_count") or 8), title, audience))
        if fmt == "pdf":
            return json.dumps(_mock_pdf(req.get("sections") or [], title, audience, req.get("page_count")))
        plan = json.loads(json.dumps(MOCK_XLSX_PLAN))
        plan["title"] = title if req else plan["title"]
        plan["include_dashboard"] = True if req.get("wants_dashboard") or not req else plan["include_dashboard"]
        return json.dumps(plan)


class GeminiLLM:
    """Google Gemini via the google-genai SDK.

    - One request timeout (from settings) on every call.
    - Up to `retries` extra attempts with a short wait, then a clear LLMError.
    - Prompts and keys are never logged. Error text never includes the key.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "",
        timeout_seconds: int = 30,
        retries: int = 2,
        wait_seconds: float = 2.0,
        client=None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if client is None:
            if not api_key:
                raise LLMError("No Gemini API key found. Put LLM_API_KEY=<your key> in .env.")
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise LLMError("The Gemini library is missing. Run: pip install google-genai") from exc
            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),  # milliseconds
            )
        self._client = client
        self.model = model or DEFAULT_GEMINI_MODEL
        self.retries = retries
        self.wait_seconds = wait_seconds
        self._sleep = sleep

    
def _config(self, system: str):
    from google.genai import types

    return types.GenerateContentConfig(
        system_instruction=system,
        response_mime_type="application/json",
        temperature=0.3,
    )


    def complete_json(self, system: str, user: str) -> str:
        last_error = "unknown error"
        for attempt in range(self.retries + 1):
            try:
                response = self._client.models.generate_content(
                    model=self.model, contents=user, config=self._config(system)
                )
                text = _extract_response_text(response)
                if text and text.strip():
                    return text
                last_error = "the model returned an empty response"
            except Exception as exc:  # network, quota, auth, timeout...
                last_error = _describe(exc)
                if _is_permanent(exc):
                    break
            if attempt < self.retries:
                self._sleep(self.wait_seconds * (attempt + 1))
        raise LLMError(f"Gemini request failed: {last_error}")


def _status_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _is_permanent(exc: Exception) -> bool:
    """Bad key / bad request will not fix themselves, so do not retry them."""
    return _status_code(exc) in (400, 401, 403, 404)


def _describe(exc: Exception) -> str:
    code = _status_code(exc)
    if code in (401, 403):
        return "the API key was rejected. Create a new key in Google AI Studio and update .env."
    if code == 404:
        return "the model name was not found. Check LLM_MODEL against the models listed in Google AI Studio."
    if code == 429:
        return "rate limit or quota reached. Wait a minute, or switch to MOCK_LLM=true while developing."
    if code == 400:
        return "the request was rejected as invalid."
    if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
        return "the request timed out."
    return f"{type(exc).__name__}"  # type only: messages may contain request details


def get_llm(settings: Settings) -> LLM:
    if settings.mock_llm or settings.llm_provider == "mock":
        return MockLLM()
    if settings.llm_provider == "gemini":
        return GeminiLLM(
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    raise LLMError(
        f"LLM provider '{settings.llm_provider}' is not supported. Use 'gemini' or 'mock'."
    )
