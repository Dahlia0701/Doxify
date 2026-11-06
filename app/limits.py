"""Input limits and the job deadline.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from app.config import Settings

MAX_PROMPT_MIN_CHARS = 5


class InputError(ValueError):
    """The user's input was rejected. The message tells them what to fix."""


class JobTimeout(Exception):
    """The job ran past its deadline. The message says in which stage."""


def check_prompt(prompt: Optional[str], settings: Settings) -> str:
    text = (prompt or "").strip()
    if not text:
        raise InputError(
            "The prompt is empty. Describe the document you want, for example: "
            "'Create a 10-slide deck about Q3 sales for executives.'"
        )
    if len(text) > settings.max_prompt_chars:
        raise InputError(
            f"The prompt is {len(text)} characters; the limit is {settings.max_prompt_chars}. "
            "Shorten it and retry."
        )
    if len(text) < MAX_PROMPT_MIN_CHARS or not any(ch.isalpha() for ch in text):
        raise InputError(
            "The prompt is too short or has no words. Say what document you want and who it is for."
        )
    return text


def check_upload(data: Optional[bytes], filename: Optional[str], settings: Settings) -> None:
    """Checks an uploaded CSV before it is parsed. None means no file was uploaded."""
    if data is None:
        return
    if filename and not filename.lower().endswith(".csv"):
        raise InputError("Only .csv files are supported. Export your data as CSV and retry.")
    if len(data) == 0:
        raise InputError("The uploaded CSV file is empty. Upload a file with a header row and data.")
    limit = settings.max_csv_mb * 1024 * 1024
    if len(data) > limit:
        size_mb = len(data) / (1024 * 1024)
        raise InputError(
            f"The CSV is {size_mb:.1f} MB; the limit is {settings.max_csv_mb} MB. "
            "Remove rows or columns and retry."
        )


class Deadline:
    """a job deadline. raises jobtimeout if time exceeded"""
    def __init__(self, seconds: float, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self.seconds = seconds
        self._end = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - self._clock())

    def check(self, stage: str) -> None:
        if self._clock() >= self._end:
            raise JobTimeout(
                f"The job took longer than {self.seconds:g} seconds (stopped before '{stage}'). "
                "Try a shorter request or a smaller file."
            )
