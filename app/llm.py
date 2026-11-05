"""LLM access: a mock (no key needed) and Google Gemini.

The LLM only ever returns JSON text. It is parsed by Pydantic and never executed.
The API key comes from settings (.env) and is never logged or put in error messages.
"""
from __future__ import annotations

import json
import time
from typing import Callable, Protocol

from app.config import Settings

DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"  # check AI Studio for current names; set LLM_MODEL to override


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


class MockLLM:
    """Returns canned JSON. Used for development and tests so nothing costs money."""

    def __init__(self, canned: str | None = None):
        self.canned = canned if canned is not None else json.dumps(MOCK_XLSX_PLAN)

    def complete_json(self, system: str, user: str) -> str:
        return self.canned


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
                text = getattr(response, "text", None)
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
