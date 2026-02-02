from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import asyncio
import subprocess
import sys
import configparser
import os

from main_mounting import mount_dataset, unmount_and_cleanup
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
        "status": "mounted and waiting for BIDSIF processing"
    }

# ---------- BIDSIF WORKER ----------
#extract total cpus and set max workers accordingly
MAX_WORKERS = os.cpu_count() or 1

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

def mountpoint_from_input_path(input_path: str, mount_id: str) -> str:
    """
    input_path example:
      /home/crpn/mnt/cifs_<mount_id>/sub_a/sub_b/dataset
    mountpoint should be:
      /home/crpn/mnt/cifs_<mount_id>
    """
    # safest: build it directly from known convention
    return f"/home/crpn/mnt/cifs_{mount_id}"

def cleanup_job_artifacts(mount_id: str, input_path: str):
    # 1) delete temp config
    tmp_cfg = Path("/tmp") / f"bids_config_{mount_id}.txt"
    try:
        tmp_cfg.unlink(missing_ok=True)
    except Exception:
        pass

    # 2) unmount and delete folder
    mountpoint = mountpoint_from_input_path(input_path, mount_id)
    unmount_and_cleanup(mountpoint)


async def bidsif_worker(worker_id: int):
    while True:
        job = await asyncio.to_thread(claim_next_job)
        if job is None:
            await asyncio.sleep(1)
            continue

        mount_id = job["mount_id"]
        input_path = job["input_path"]

        try:
            cfg = await asyncio.to_thread(prepare_bids_config, job)
            result = await asyncio.to_thread(run_bidsif, cfg)

            # Safety check: output exists
            expected = Path(job["output_path"]) / "participants.tsv"
            if result.returncode == 0 and expected.exists():
                await asyncio.to_thread(set_job_status, mount_id, "success", None)
            else:
                err = (result.stderr or result.stdout or "").strip()
                if not err and not expected.exists():
                    err = f"Output missing: {expected}"
                await asyncio.to_thread(set_job_status, mount_id, "failed", err[:2000])

        except Exception as e:
            await asyncio.to_thread(set_job_status, mount_id, "failed", str(e)[:2000])

        finally:
            # Always cleanup mount and temp config (success or fail)
            try:
                await asyncio.to_thread(cleanup_job_artifacts, mount_id, input_path)
            except Exception as e:
                # Don't crash the worker; record cleanup error (optional)
                await asyncio.to_thread(
                    set_job_status,
                    mount_id,
                    "failed",
                    f"Cleanup error: {e}"[:2000]
                )


# ---------- STARTUP ----------

@app.on_event("startup")
async def startup():
    init_db()
    for i in range(MAX_WORKERS):
        asyncio.create_task(bidsif_worker(i + 1))
