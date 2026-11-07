"""prompt/upload limits and deadline checks """
import pytest

from app.config import Settings
from app.limits import Deadline, InputError, JobTimeout, check_prompt, check_upload

S = Settings(max_prompt_chars=100, max_csv_mb=1)


def test_good_prompt_is_trimmed():
    assert check_prompt("  Create a 10-slide deck  ", S) == "Create a 10-slide deck"


@pytest.mark.parametrize("bad", [None, "", "   ", "\n\t"])
def test_empty_prompt_rejected_with_example(bad):
    with pytest.raises(InputError, match="empty"):
        check_prompt(bad, S)


def test_too_long_prompt_rejected():
    with pytest.raises(InputError, match="limit is 100"):
        check_prompt("a" * 101, S)


@pytest.mark.parametrize("bad", ["hi", "12345 678", "!!!!!!!!"])
def test_nonsense_prompt_rejected(bad):
    with pytest.raises(InputError):
        check_prompt(bad, S)


def test_no_upload_is_fine():
    check_upload(None, None, S)


def test_empty_csv_rejected():
    with pytest.raises(InputError, match="empty"):
        check_upload(b"", "data.csv", S)


def test_oversized_csv_rejected():
    with pytest.raises(InputError, match="limit is 1 MB"):
        check_upload(b"x" * (1024 * 1024 + 1), "data.csv", S)


def test_non_csv_rejected():
    with pytest.raises(InputError, match=r"\.csv"):
        check_upload(b"a,b\n1,2", "data.xlsx", S)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_deadline_passes_then_raises_with_stage_name():
    clock = FakeClock()
    deadline = Deadline(10, clock=clock)
    deadline.check("planning")  # fine at t=0
    clock.now = 9.9
    deadline.check("rendering")  # still fine
    clock.now = 10.0
    with pytest.raises(JobTimeout, match="rendering|validating"):
        deadline.check("validating")
    assert deadline.remaining() == 0.0
