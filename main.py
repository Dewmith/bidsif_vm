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
    append_error
)


# -----------------------------------------------------------------------------
# FastAPI app entrypoint for the VM project
#
# Two big responsibilities live here:
# 1) HTTP API:
#    - /submit mounts dataset and queues a job in SQLite
#    - /mounts returns the job list for the UI
#    - / serves the web UI
#
# 2) Background worker(s):
#    - Many asyncio tasks polling SQLite for queued jobs (claim_next_job)
#    - For each job:
#        - prepare config file
#        - run BIDSIF
#        - validate success
#        - set DB status
#        - cleanup (unmount, delete temp config)
#
# Key concurrency idea:
# - /submit can be called by many users at once
# - Jobs are queued in DB
# - Workers claim jobs one-by-one atomically using SQLite transactions
# -----------------------------------------------------------------------------


app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

#BIDSIF location (might have to change for reusability)
BIDSIF_DIR = (BASE_DIR.parent / "bidsif").resolve()
BIDSIF_SCRIPT = BIDSIF_DIR / "bidsify.py"
BIDSIF_PYTHON = BIDSIF_DIR / ".venv_bidsif" / "bin" / "python"

# Fail early if BIDSIF environment isn't present
if not BIDSIF_PYTHON.exists():
    raise RuntimeError(f"BIDSIF venv python not found at {BIDSIF_PYTHON}")
if not BIDSIF_SCRIPT.exists():
    raise RuntimeError(f"Script not found at {BIDSIF_SCRIPT}")

# Serve static frontend assets
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# -----------------------------------------------------------------------------
# Request model (what the UI/client sends on /submit)
# -----------------------------------------------------------------------------


class SubmitRequest(BaseModel):
    unc: str
    subdir: str
    username: str
    password: str

# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------


@app.get("/")
def home():
    """Serve the web UI homepage."""
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/mounts")
def get_mounts():
    """
    Return recent jobs to the UI.
    This returns user-friendly refs (no internal VM paths).
    """
    return list_mounts()

@app.post("/submit")
async def submit(req: SubmitRequest):
    """
    1) Mount the UNC path into the VM (runs in a thread because it blocks)
    2) Compute output paths and user-friendly refs
    3) Insert a 'queued' job into DB
    4) Return job info to UI
    """
    try:
        mount_id, mountpoint, access_path = await asyncio.to_thread(
            mount_dataset,
            req.unc,
            req.username,
            req.password,
            req.subdir
        )
    except Exception as e:
        # Any mount failure is reported as HTTP 400 for the client
        raise HTTPException(status_code=400, detail=str(e))

    # Internal paths used by the worker
    output_path = compute_output_path(mountpoint, req.subdir)
    
    # User-visible "ref" paths shown in UI
    input_ref = compute_input_ref(req.unc, req.subdir)
    output_ref = compute_output_ref(req.unc, req.subdir)

    # Store job in DB (queue it)
    insert_mount_success(
        mount_id=mount_id,
        username=req.username.strip(),
        unc=req.unc.strip(),
        subdir=req.subdir.strip(),
        input_path=access_path,
        output_path=output_path,
        input_ref=input_ref,
        output_ref=output_ref,
        status="queued"
    )

    # Return a summary to the UI
    return {
        "mount_id": mount_id,
        "created_at": now_iso(),
        "username": req.username.strip(),
        "subdir": req.subdir.strip(),
        "input_ref": input_ref,
        "output_ref": output_ref,
        "status": "queued",
    }


# -----------------------------------------------------------------------------
# BIDSIF worker machinery
# -----------------------------------------------------------------------------


# Number of worker tasks spawned.
# NOTE: This is NOT a threadpool size; it's the number of asyncio tasks. (How many datsets to BIDSIFy in parallel.)
MAX_WORKERS = os.cpu_count() or 1

def prepare_bids_config(job: dict) -> Path:
    """
    Prepare a temporary BIDSIF config file for this job by:
    - reading bids_configurator.txt from the mounted dataset
    - overriding input_path/output_path in DEFAULT section
    - writing a /tmp/bids_config_<mount_id>.txt

    This keeps BIDSIF "per job" and avoids editing the dataset.
    """
    input_path = Path(job["input_path"])
    output_path = Path(job["output_path"])

    template = input_path / "bids_configurator.txt"
    if not template.exists():
        raise FileNotFoundError(f"Missing config file: {template}")


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
    """
    Execute BIDSIF (bidsify.py) using the dedicated virtualenv python.
    capture_output=True collects stdout/stderr for logging on failure.
    """
    return subprocess.run(
        [str(BIDSIF_PYTHON), str(BIDSIF_SCRIPT), "--config", str(config_path)],
        cwd=str(BIDSIF_DIR),
        capture_output=True,
        text=True
    )

def mountpoint_from_input_path(input_path: str, mount_id: str) -> str:
    """
    Derive the mountpoint from the mount_id.
    The input_path may include subdirectories; mountpoint is always:
      /home/crpn/mnt/cifs_<mount_id>

    NOTE: This relies on the convention used by main_mounting.py.
    """
    # safest: build it directly from known convention
    return f"/home/crpn/mnt/cifs_{mount_id}"

def cleanup_job_artifacts(mount_id: str, input_path: str):
    """
    Cleanup after a job (success or fail):
    1) remove /tmp/bids_config_<mount_id>.txt
    2) unmount and remove mountpoint directory
    """
    tmp_cfg = Path("/tmp") / f"bids_config_{mount_id}.txt"
    try:
        tmp_cfg.unlink(missing_ok=True)
    except Exception:
        pass

    # 2) unmount and delete folder
    mountpoint = mountpoint_from_input_path(input_path, mount_id)
    unmount_and_cleanup(mountpoint)


async def bidsif_worker(worker_id: int):
    """
    Infinite worker loop:
    - Claim a queued job from the DB (atomic)
    - Prepare config
    - Run BIDSIF
    - Validate success by checking participants.tsv
    - Update job status (success/failed)
    - Always cleanup mount and temp files
    """
    while True:
        # Claiming uses SQLite writes → blocking → move into a thread
        job = await asyncio.to_thread(claim_next_job)
        
        # If nothing is queued, wait and poll again
        if job is None:
            await asyncio.sleep(1)
            continue

        mount_id = job["mount_id"]
        input_path = job["input_path"]

        try:
            # Prepare temp config
            cfg = await asyncio.to_thread(prepare_bids_config, job)
            
            # Run BIDSIF
            result = await asyncio.to_thread(run_bidsif, cfg)

            # Simple "did it work?" validation:
            # if BIDSIF says success AND expected file exists
            expected = Path(job["output_path"]) / "participants.tsv"
            if result.returncode == 0 and expected.exists():
                await asyncio.to_thread(set_job_status, mount_id, "success", None)
            else:
                stderr = (result.stderr or "").strip()
                stdout = (result.stdout or "").strip()

                err = f"BIDSIF exited with code {result.returncode}."
                if stderr:
                    err += f"\n--- STDERR ---\n{stderr}"
                if stdout:
                    err += f"\n--- STDOUT ---\n{stdout}"

                await asyncio.to_thread(set_job_status, mount_id, "failed", err[:2000])


        except Exception as e:
            # Any unexpected crash becomes "failed"
            await asyncio.to_thread(set_job_status, mount_id, "failed", str(e)[:2000])

        finally:
            # Always cleanup mount and temp config (success or fail)
            try:
                await asyncio.to_thread(cleanup_job_artifacts, mount_id, input_path)
            except Exception as e:
                # Keep the existing status/error. Just append cleanup info.
                await asyncio.to_thread(append_error, mount_id, f"Cleanup error: {e}")



# -----------------------------------------------------------------------------
# Startup: init DB + launch worker tasks
# -----------------------------------------------------------------------------


@app.on_event("startup")
async def startup():
    """
    At API startup:
    - Create DB schema
    - Launch worker tasks (MAX_WORKERS)
    """
    init_db()
    for i in range(MAX_WORKERS):
        asyncio.create_task(bidsif_worker(i + 1))
