""" gemini retries,errors,fake responses and secret-safe settings"""
import pytest

from app.config import Settings
from app.llm import GeminiLLM, LLMError, MockLLM, get_llm


class FakeResponse:
    def __init__(self, text):
        self.text = text


class FakeModels:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate_content(self, model, contents, config):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)


class FakeClient:
    def __init__(self, outcomes):
        self.models = FakeModels(outcomes)


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(f"http {code} secret-detail")
        self.code = code


def make(outcomes, retries=2):
    client = FakeClient(outcomes)
    llm = GeminiLLM(api_key="k", client=client, retries=retries, sleep=lambda s: None)
    return llm, client


def test_success_first_try():
    llm, client = make(['{"ok": true}'])
    assert llm.complete_json("sys", "user") == '{"ok": true}'
    assert client.models.calls == 1


def test_retries_then_succeeds():
    llm, client = make([TimeoutError("timed out"), HttpError(503), '{"ok": 1}'])
    assert llm.complete_json("s", "u") == '{"ok": 1}'
    assert client.models.calls == 3


def test_gives_up_after_retries_with_clear_error():
    llm, client = make([TimeoutError("timed out")] * 3)
    with pytest.raises(LLMError, match="timed out"):
        llm.complete_json("s", "u")
    assert client.models.calls == 3  # 1 try + 2 retries, then stop


def test_bad_key_is_not_retried_and_message_is_actionable():
    llm, client = make([HttpError(403)])
    with pytest.raises(LLMError, match="API key was rejected"):
        llm.complete_json("s", "u")
    assert client.models.calls == 1


def test_error_message_does_not_leak_exception_details():
    llm, _ = make([HttpError(500)] * 3)
    with pytest.raises(LLMError) as info:
        llm.complete_json("s", "u")
    assert "secret-detail" not in str(info.value)


def test_empty_response_is_an_error():
    llm, _ = make(["", "  ", None])
    with pytest.raises(LLMError, match="empty"):
        llm.complete_json("s", "u")


def test_missing_key_gives_actionable_error():
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        GeminiLLM(api_key="")


def test_get_llm_picks_mock_and_gemini():
    assert isinstance(get_llm(Settings(mock_llm=True)), MockLLM)
    with pytest.raises(LLMError, match="LLM_API_KEY"):
        get_llm(Settings(mock_llm=False, llm_provider="gemini", llm_api_key=""))
    with pytest.raises(LLMError, match="not supported"):
        get_llm(Settings(mock_llm=False, llm_provider="other"))


def test_settings_repr_hides_key():
    assert "secret123" not in repr(Settings(llm_api_key="secret123"))
