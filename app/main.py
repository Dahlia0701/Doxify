from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.config import load_settings
from app.limits import InputError
from app.pipeline import GenerationRequest, MEDIA_TYPES
from app.service import JobService


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = JobService(load_settings())
    yield
    app.state.service.executor.shutdown(wait=False)


app = FastAPI(title="Doxify", lifespan=lifespan)


def get_service():
    return app.state.service


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/jobs", status_code=202)
async def create_job(prompt: str = Form(...), fmt: str = Form("auto"),
                     csv: UploadFile | None = File(None),
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key")):
    service = get_service()
    if csv is not None:
        limit = service.settings.max_csv_mb * 1024 * 1024
        data = await csv.read(limit + 1)
        filename = csv.filename
    else:
        data, filename = None, None
    try:
        job = service.submit(GenerationRequest(prompt, fmt, data, filename), idempotency_key)
    except InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": job["id"], "status": job["status"], "status_url": f"/jobs/{job['id']}"}


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = get_service().status(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return {k: v for k, v in job.items() if k not in ("path", "idem_key")}


@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    try:
        path = get_service().download(job_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix.lstrip(".")], filename=path.name)


static_dir = Path(__file__).resolve().parent.parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
