"""Settings read from environment / .env. No secrets are ever hard-coded or logged."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "mock"
    llm_api_key: str = ""
    llm_model: str = ""
    mock_llm: bool = True
    max_csv_mb: int = 5
    max_prompt_chars: int = 4000
    job_timeout_seconds: int = 120
    llm_timeout_seconds: int = 30
    max_output_mb: int = 20
    max_rows_per_sheet: int = 5000

    def __repr__(self) -> str:  # never print the key
        return (
            f"Settings(provider={self.llm_provider!r}, model={self.llm_model!r}, "
            f"mock={self.mock_llm}, api_key={'set' if self.llm_api_key else 'unset'})"
        )


def load_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "mock").lower(),
        llm_api_key=os.getenv("LLM_API_KEY", ""),
        llm_model=os.getenv("LLM_MODEL", ""),
        mock_llm=os.getenv("MOCK_LLM", "true").lower() in ("1", "true", "yes"),
        max_csv_mb=_int("MAX_CSV_MB", 5),
        max_prompt_chars=_int("MAX_PROMPT_CHARS", 4000),
        job_timeout_seconds=_int("JOB_TIMEOUT_SECONDS", 120),
        llm_timeout_seconds=_int("LLM_TIMEOUT_SECONDS", 30),
        max_output_mb=_int("MAX_OUTPUT_MB", 20),
        max_rows_per_sheet=_int("MAX_ROWS_PER_SHEET", 5000),
    )
