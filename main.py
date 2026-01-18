from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path
import asyncio

from main_mounting import mount_dataset
from db import (
    init_db,
    insert_mount_success,
    compute_output_path,
    compute_input_ref,
    compute_output_ref,
    list_mounts,
    now_iso,
)

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

# ---------- Methods ----------
def compute_reference_output_path(unc: str, subdir: str) -> str:
    """
    Returns something user-friendly like:
      Users/Weerasena_D/sub_a/sub_b/BIDSIFied_dewmith_w

    We take the UNC and extract the part starting from "Users" (case-insensitive)
    then append the parent folders of subdir and finally BIDSIFied_<last>.
    """
    unc_clean = (unc or "").strip().replace("\\", "/").strip("/")
    parts = unc_clean.split("/")

    # Find "Users" segment
    idx = None
    for i, p in enumerate(parts):
        if p.lower() == "users":
            idx = i
            break

    base = "/".join(parts[idx:] if idx is not None else parts[-2:])  # fallback

    sub_clean = (subdir or "").strip().replace("\\", "/").strip("/")
    if not sub_clean:
        return f"{base}/BIDSIFied_ROOT"

    sub_parts = sub_clean.split("/")
    parent = "/".join(sub_parts[:-1])
    last = sub_parts[-1]

    if parent:
        return f"{base}/{parent}/BIDSIFied_{last}"
    return f"{base}/BIDSIFied_{last}"


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
    input_ref = compute_input_ref(req.unc, req.subdir)
    output_ref = compute_output_ref(req.unc, req.subdir)

    insert_mount_success(
        mount_id=mount_id,
        username=req.username.strip(),
        unc=req.unc.strip(),
        subdir=req.subdir.strip(),
        input_path=access_path,      # VM path hidden from user
        output_path=output_path,     # VM path hidden from user
        input_ref=input_ref,         # user-visible
        output_ref=output_ref,       # user-visible
        status="mounted"
    )

    # Return ONLY user-visible fields:
    return {
        "mount_id": mount_id,
        "created_at": now_iso(),    # optional
        "username": req.username.strip(),
        "subdir": req.subdir.strip(),
        "input_ref": input_ref,
        "output_ref": output_ref,
        "status": "mounted"
    }




# ---------- STARTUP ----------

@app.on_event("startup")
async def startup():
    init_db()
