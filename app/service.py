"""Business logic independent of FastAPI, testing done with a mock LLM."""
import hashlib
import json
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import Settings
from app.jobs import JobStore
from app.limits import InputError, JobTimeout, check_prompt, check_upload
from app.llm import LLMError, get_llm
from app.planner import PlanError
from app.pipeline import RenderError
from app.pipeline import GenerationRequest, generate_document

log = logging.getLogger("doxify.service")


class JobService:
    def __init__(self, settings: Settings, llm=None, executor=None):
        self.settings = settings
        self.root = Path(settings.data_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.root / "jobs.sqlite3")
        self.llm = llm if llm is not None else get_llm(settings)
        self.executor = executor or ThreadPoolExecutor(max_workers=max(1, settings.max_workers))

    def submit(self, request: GenerationRequest, idem_key=None):
        check_prompt(request.prompt, self.settings)
        check_upload(request.csv_bytes, request.csv_name, self.settings)
        if request.fmt not in (None, "auto", "pdf", "pptx", "xlsx"):
            raise InputError("Choose pdf, pptx, xlsx or auto.")
        # A retry with the same key and different content must not silently reuse an unrelated job.
        fingerprint = hashlib.sha256(json.dumps({
            "prompt": request.prompt.strip(), "fmt": request.fmt,
            "csv_sha256": hashlib.sha256(request.csv_bytes or b"").hexdigest(),
            "csv_name": request.csv_name or ""
        }, sort_keys=True).encode()).hexdigest()
        key = f"{idem_key}:{fingerprint}" if idem_key else fingerprint
        job_id, created = self.store.create(key)
        if created:
            self.executor.submit(self._run, job_id, request)
        return self.store.get(job_id)

    def _run(self, job_id, request):
        if not self.store.claim(job_id):
            return
        folder = self.root / "outputs" / job_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            result = generate_document(request, self.settings, self.llm, folder,
                                       job_id=job_id, on_stage=lambda stage: self.store.stage(job_id, stage))
            if result.path and result.path.stat().st_size > self.settings.max_output_mb * 1024 * 1024:
                raise InputError("The generated file exceeds the output size limit. Try a smaller document.")
            self.store.finish(job_id, result)
        except (InputError, JobTimeout, LLMError, PlanError, RenderError) as exc:
            self.store.fail(job_id, str(exc))
        except Exception as exc:
            log.exception("job %s failed with %s", job_id, type(exc).__name__)
            self.store.fail(job_id, "Generation failed. Please try a simpler request or check the server logs.")
        finally:
            # Files from unsuccessful jobs are never downloadable.
            job = self.store.get(job_id)
            if job and job["status"] == "failed":
                shutil.rmtree(folder, ignore_errors=True)

    def status(self, job_id):
        return self.store.get(job_id)

    def download(self, job_id):
        job = self.status(job_id)
        if job is None:
            raise FileNotFoundError("Job not found")
        if job["status"] not in ("completed", "completed_with_warnings"):
            raise ValueError("The file is not ready for download")
        path = Path(job["path"] or "")
        # Only serve files inside the job's own output folder.
        folder = (self.root / "outputs" / job_id).resolve()
        if not path.is_file() or not path.resolve().is_relative_to(folder):
            raise FileNotFoundError("Generated file is missing")
        return path
