from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import asyncio

from main_mounting import mount_dataset
from db import init_db, insert_mount_success, compute_output_path, list_mounts

app = FastAPI()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------- MODELS ----------

class SubmitRequest(BaseModel):
    unc: str
    subdir: str = ""
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
    # 1) MOUNT IMMEDIATELY
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

    # 2) COMPUTE OUTPUT PATH
    output_path = compute_output_path(mountpoint, req.subdir)

    # 3) WRITE TO DB (ONLY AFTER SUCCESSFUL MOUNT)
    insert_mount_success(
        mount_id=mount_id,
        username=req.username,
        unc=req.unc,
        subdir=req.subdir,
        access_path=access_path,
        output_path=output_path,
        status="mounted"
    )

    return {
        "mount_id": mount_id,
        "mountpoint": mountpoint,
        "access_path": access_path,
        "output_path": output_path,
        "status": "mounted"
    }


# ---------- STARTUP ----------

@app.on_event("startup")
async def startup():
    init_db()
