"""Shared validation types.

critical -> the file is unusable or missing required content -> job status 'failed'
warning  -> file is usable but has a minor unresolved issue   -> 'completed_with_warnings'
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MAX_OUTPUT_BYTES = 20 * 1024 * 1024


@dataclass
class Issue:
    severity: str  # "critical" | "warning"
    code: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)

    def add(self, severity: str, code: str, message: str) -> None:
        self.issues.append(Issue(severity, code, message))

    @property
    def critical(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "critical"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def status(self) -> str:
        if self.critical:
            return "failed"
        if self.warnings:
            return "completed_with_warnings"
        return "completed"


def check_file_basic(path: str | Path, result: ValidationResult, max_bytes: int = MAX_OUTPUT_BYTES) -> bool:
    """Returns True if the file exists, is non-empty and under the size limit."""
    p = Path(path)
    if not p.exists():
        result.add("critical", "file_missing", f"Output file was not created: {p.name}")
        return False
    size = os.path.getsize(p)
    if size == 0:
        result.add("critical", "file_empty", "Output file is empty (0 bytes).")
        return False
    if size > max_bytes:
        result.add("critical", "file_too_large", f"Output is {size} bytes, over the {max_bytes} limit.")
        return False
    return True
