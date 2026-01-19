from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import asyncio
import subprocess
import sys
import configparser

from main_mounting import mount_dataset
from db import (
    init_db,
    insert_mount_success,
    compute_output_path,
    compute_input_ref,
    compute_output_ref,
    list_mounts,
    now_iso,
    claim_next_job,
    set_job_status,
)

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

#BIDSIF location (might have to change for reusability)
BIDSIF_DIR = (BASE_DIR.parent / "bidsif").resolve()
BIDSIF_SCRIPT = BIDSIF_DIR / "bidsify.py"
BIDSIF_PYTHON = BIDSIF_DIR / ".venv_bidsif" / "bin" / "python"

if not BIDSIF_PYTHON.exists():
    raise RuntimeError(f"BIDSIF venv python not found at {BIDSIF_PYTHON}")
if not BIDSIF_SCRIPT.exists():
    raise RuntimeError(f"Script not found at {BIDSIF_SCRIPT}")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ---------- MODELS ----------

class SubmitRequest(BaseModel):
    unc: str
    subdir: str
    username: str
    password: str

# ---------- ROUTES ----------

@app.get("/")
def home():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/mounts")
def get_mounts():
    return list_mounts()

@app.post("/submit")
async def submit(req: SubmitRequest):
    try:
        mount_id, mountpoint, access_path = await asyncio.to_thread(
            mount_dataset,
            req.unc,
            req.username,
            req.password,
            req.subdir
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    output_path = compute_output_path(mountpoint, req.subdir)
    input_ref = compute_input_ref(req.unc, req.subdir)
    output_ref = compute_output_ref(req.unc, req.subdir)

    insert_mount_success(
        mount_id=mount_id,
        username=req.username.strip(),
        unc=req.unc.strip(),
        subdir=req.subdir.strip(),
        input_path=access_path,
        output_path=output_path,
        input_ref=input_ref,
        output_ref=output_ref,
        status="mounted"
    )

    return {
        "mount_id": mount_id,
        "created_at": now_iso(),
        "username": req.username.strip(),
        "subdir": req.subdir.strip(),
        "input_ref": input_ref,
        "output_ref": output_ref,
        "status": "mounted"
    }

# ---------- BIDSIF WORKER ----------

MAX_WORKERS = 4

def prepare_bids_config(job: dict) -> Path:
    input_path = Path(job["input_path"])
    output_path = Path(job["output_path"])

    template = input_path / "bids_configurator.txt"
    if not template.exists():
        raise FileNotFoundError(f"bids_configurator.txt not found in {input_path}")

    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(template)

    if "DEFAULT" not in cfg:
        cfg["DEFAULT"] = {}

    cfg["DEFAULT"]["input_path"] = str(input_path)
    cfg["DEFAULT"]["output_path"] = str(output_path)

    output_path.mkdir(parents=True, exist_ok=True)

    tmp_cfg = Path("/tmp") / f"bids_config_{job['mount_id']}.txt"
    with open(tmp_cfg, "w") as f:
        cfg.write(f)

    return tmp_cfg

def run_bidsif(config_path: Path):
    return subprocess.run(
        [str(BIDSIF_PYTHON), str(BIDSIF_SCRIPT), "--config", str(config_path)],
        cwd=str(BIDSIF_DIR),
        capture_output=True,
        text=True
    )


async def bidsif_worker(worker_id: int):
    while True:
        job = await asyncio.to_thread(claim_next_job)
        if job is None:
            await asyncio.sleep(1)
            continue

        mount_id = job["mount_id"]
        try:
            cfg = await asyncio.to_thread(prepare_bids_config, job)
            result = await asyncio.to_thread(run_bidsif, cfg)

            if result.returncode == 0:
                await asyncio.to_thread(set_job_status, mount_id, "success")
            else:
                err = (result.stderr or result.stdout or "").strip()
                await asyncio.to_thread(set_job_status, mount_id, "failed", err[:2000])

        except Exception as e:
            await asyncio.to_thread(set_job_status, mount_id, "failed", str(e)[:2000])

# ---------- STARTUP ----------

@app.on_event("startup")
async def startup():
    init_db()
    for i in range(MAX_WORKERS):
        asyncio.create_task(bidsif_worker(i + 1))
