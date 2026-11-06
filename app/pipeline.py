"""The generation pipeline: prompt -> intent -> plan (IR) -> render -> validate -> bounded repair.
"""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.config import Settings
from app.data_profile import build_from_profile, claims_for, load_csv, profile_dataframe
from app.intent import Intent, parse_intent
from app.limits import Deadline, InputError, check_prompt, check_upload
from app.llm import LLM, LLMError
from app.planner import PlanError, parse_plan
from app.prompts import system_prompt, user_prompt
from app.repair import repair
from app.schemas import AnyDoc
from renderers.pdf_renderer import render_pdf
from renderers.pptx_renderer import render_pptx
from renderers.xlsx_renderer import render_xlsx
from validators.common import ValidationResult
from validators.content_validator import validate_metric_claims
from validators.pdf_validator import validate_pdf
from validators.pptx_validator import validate_pptx
from validators.requirements import check_requirements
from validators.text_checks import check_plan_text
from validators.xlsx_validator import check_calculated_values, validate_xlsx

log = logging.getLogger("doxify.pipeline")

STAGES = ("understanding", "planning", "rendering", "validating", "repairing")
EXT = {"pdf": "pdf", "pptx": "pptx", "xlsx": "xlsx"}
MEDIA_TYPES = {
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
PLAN_ATTEMPTS = 2  


class RenderError(Exception):
    """The renderer crashed. Message is user-facing and contains no internals."""


@dataclass
class GenerationRequest:
    prompt: str
    fmt: Optional[str] = None  # 'pdf' | 'pptx' | 'xlsx' | 'auto' | None
    csv_bytes: Optional[bytes] = None
    csv_name: Optional[str] = None


@dataclass
class GenerationResult:
    status: str  # completed | completed_with_warnings | failed
    fmt: str
    title: str
    path: Optional[Path]
    issues: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    checks_skipped: list[str] = field(default_factory=list)
    intent: dict = field(default_factory=dict)
    render_attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.status in ("completed", "completed_with_warnings")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "document"


def _render(doc: AnyDoc, fmt: str, path: Path) -> None:
    try:
        {"pdf": render_pdf, "pptx": render_pptx, "xlsx": render_xlsx}[fmt](doc, path)
    except Exception as exc:  # never leak internals to the user
        log.error("render failed fmt=%s error=%s", fmt, type(exc).__name__)
        raise RenderError(
            f"The {fmt.upper()} file could not be generated ({type(exc).__name__}). "
            "Simplify the request or try again."
        ) from exc


def _validate(doc: AnyDoc, fmt: str, path: Path, intent: Intent, deadline: Deadline, skipped: list[str],
              extra: Optional[ValidationResult]) -> ValidationResult:
    if fmt == "pdf":
        result = validate_pdf(path, doc, requested_pages=intent.page_count)
    elif fmt == "pptx":
        result = validate_pptx(path, doc)  
    else:
        result = validate_xlsx(path, doc)
        if not result.critical:
            ran = check_calculated_values(path, result, timeout=int(max(5, min(30, deadline.remaining()))))
            if not ran and "formula recalculation (LibreOffice unavailable)" not in skipped:
                skipped.append("formula recalculation (LibreOffice unavailable)")
    result.extend(check_requirements(doc, intent))
    check_plan_text(doc, result)
    if extra is not None:
        result.extend(extra)
    return result


def _plan_with_llm(prompt: str, intent: Intent, llm: LLM, deadline: Deadline) -> AnyDoc:
    """Ask the model for a plan. One retry, with the rejection reason, if the answer is unusable."""
    feedback: Optional[str] = None
    best: Optional[AnyDoc] = None
    last_error: Optional[PlanError] = None
    for attempt in range(PLAN_ATTEMPTS):
        deadline.check("planning")
        raw = llm.complete_json(system_prompt(intent.fmt), user_prompt(prompt, intent, feedback))
        try:
            doc = parse_plan(raw, intent.fmt)
        except PlanError as exc:
            last_error, feedback = exc, str(exc)
            continue
        best, last_error = doc, None
        problems = [i.message for i in check_requirements(doc, intent).critical]
        if not problems:
            return doc
        feedback = " ".join(problems)
    if best is not None:
        return best  
    assert last_error is not None
    raise last_error


def generate_document(
    req: GenerationRequest,
    settings: Settings,
    llm: LLM,
    out_dir: Path,
    *,
    deadline: Optional[Deadline] = None,
    on_stage: Callable[[str], None] = lambda s: None,
    job_id: str = "-",
) -> GenerationResult:
    """Run the whole pipeline. Raises InputError, PlanError, LLMError, JobTimeout or RenderError;
    returns a GenerationResult (possibly status='failed') otherwise."""
    deadline = deadline or Deadline(settings.job_timeout_seconds)
    out_dir = Path(out_dir)

    on_stage("understanding")
    prompt = check_prompt(req.prompt, settings)
    check_upload(req.csv_bytes, req.csv_name, settings)
    intent = parse_intent(prompt, req.fmt, has_csv=req.csv_bytes is not None)
    fmt = intent.fmt
    log.info("job=%s format=%s source=%s", job_id, fmt, intent.fmt_source)

    on_stage("planning")
    deadline.check("planning")
    extra: Optional[ValidationResult] = None
    if req.csv_bytes is not None:
        prof = profile_dataframe(load_csv(req.csv_bytes, settings))
        doc = build_from_profile(prof, intent,prompt)
        extra = validate_metric_claims(claims_for(prof), prof.metrics)
        
        extra.issues = [i for i in extra.issues if i.code != "prose_unverified"]
    else:
        doc = _plan_with_llm(prompt, intent, llm, deadline)

    out_path = out_dir / f"{_slug(doc.title)}.{EXT[fmt]}"
    repairs: list[str] = []
    skipped: list[str] = []
    result = ValidationResult()
    attempts = 0
    for attempt in range(settings.max_repairs + 1):
        attempts += 1
        on_stage("rendering")
        deadline.check("rendering")
        _render(doc, fmt, out_path)
        on_stage("validating")
        deadline.check("validating")
        result = _validate(doc, fmt, out_path, intent, deadline, skipped, extra)
        if not result.issues or attempt == settings.max_repairs:
            break
        on_stage("repairing")
        outcome = repair(doc, result.issues, intent, attempt=attempt + 1)
        if not outcome.changed:
            break  #reporting failure
        doc = outcome.doc
        repairs.extend(outcome.actions)
        out_path = out_dir / f"{_slug(doc.title)}.{EXT[fmt]}"

    status = result.status
    if status == "failed":
        out_path.unlink(missing_ok=True)  
    issues = [{"severity": i.severity, "code": i.code, "message": i.message} for i in result.issues]
    log.info("job=%s status=%s issues=%d repairs=%d", job_id, status, len(issues), len(repairs))
    return GenerationResult(
        status=status,
        fmt=fmt,
        title=doc.title,
        path=None if status == "failed" else out_path,
        issues=issues,
        assumptions=list(dict.fromkeys(intent.assumptions + list(getattr(doc, "assumptions", [])))),
        repairs=repairs,
        checks_skipped=skipped,
        intent=intent.to_dict(),
        render_attempts=attempts,
    )
