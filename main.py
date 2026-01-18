from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pathlib import Path
from datetime import datetime, timezone
import itertools

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if not STATIC_DIR.exists():
    raise RuntimeError(f"Missing static directory: {STATIC_DIR}")

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
def homepage():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/ping")
def ping():
    return {"message": "BIDSIF API is alive"}

# ---------- Jobs (in-memory for now) ----------

class CreateJobRequest(BaseModel):
    unc: str = Field(..., description="UNC path to mount, e.g. //server/share")
    subdir: str = Field("", description="Subdirectory inside share (optional)")
    username: str = Field(..., description="SMB username")
    password: str = Field(..., description="SMB password (never stored)")

class Job(BaseModel):
    job_id: int
    created_at: str
    status: str  # queued|running|success|failed (for now)
    unc: str
    subdir: str = ""
    username: str
    error: str | None = None

_job_id_counter = itertools.count(1)
_jobs: list[Job] = []

def now_iso():
    return datetime.now(timezone.utc).isoformat()

@app.post("/jobs", response_model=Job)
def create_job(req: CreateJobRequest):
    # Basic validation (front-end already checks, but backend must too)
    if not req.unc.strip():
        raise HTTPException(status_code=400, detail="unc is required")
    if not req.username.strip():
        raise HTTPException(status_code=400, detail="username is required")
    if not req.password:
        raise HTTPException(status_code=400, detail="password is required")

    job = Job(
        job_id=next(_job_id_counter),
        created_at=now_iso(),
        status="queued",
        unc=req.unc.strip(),
        subdir=(req.subdir or "").strip(),
        username=req.username.strip(),
        error=None,
    )

    _jobs.insert(0, job)  # newest first for UI convenience
    return job

@app.get("/jobs", response_model=list[Job])
def list_jobs():
    return _jobs
