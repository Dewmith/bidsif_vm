# db.py
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo


# -----------------------------------------------------------------------------
# SQLite database layer for the VM project
#
# Purpose:
# - Store mount “jobs” submitted by users (UNC + credentials result -> internal VM paths)
# - Track job status (queued/running/success/failed) and error messages
# - Provide a simple queue mechanism using SQLite transactions ("claim_next_job")
#
# IMPORTANT DESIGN:
# - The API should only return "user-friendly" paths (input_ref/output_ref)
# - The actual VM paths (input_path/output_path) remain hidden from the user
# -----------------------------------------------------------------------------


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bidsif.db"


PARIS_TZ = ZoneInfo("Europe/Paris")

def now_iso() -> str:
    """
    Return "now" as an ISO-8601 timestamp with millisecond precision,
    using Europe/Paris timezone.
    """
    return datetime.now(PARIS_TZ).isoformat(timespec="milliseconds")



def get_conn() -> sqlite3.Connection:
    """
    Open a SQLite connection to bidsif.db.

    row_factory=sqlite3.Row lets us access columns by name (row["status"])
    and makes conversion to dict easier.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    """
    Create the mounts table and useful indexes (if not already created).
    This is run once at API startup.
    """
    with get_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mounts (
                mount_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,

                username TEXT NOT NULL,
                unc TEXT NOT NULL,
                subdir TEXT NOT NULL,

                input_path TEXT NOT NULL,     -- VM path used by code (hidden from user)
                output_path TEXT NOT NULL,    -- VM path used by code (hidden from user)

                input_ref TEXT NOT NULL,      -- user-friendly reference path
                output_ref TEXT NOT NULL,     -- user-friendly reference path

                status TEXT NOT NULL,
                error TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_mounts_created_at ON mounts(created_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mounts_username ON mounts(username)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mounts_status ON mounts(status)")
        con.commit()


def compute_output_path(mountpoint: str, subdir: str) -> str:
    """
    Compute the INTERNAL output folder path on the VM.

    Example:
      subdir: sub_a/sub_b/dataset
      mountpoint: /home/crpn/mnt/cifs_<id>
      output: /home/crpn/mnt/cifs_<id>/sub_a/sub_b/BIDSIFied_dataset

    If subdir is empty:
      output: mountpoint/BIDSIFied_ROOT
    """
    clean = (subdir or "").strip().replace("\\", "/").strip("/")
    if not clean:
        return f"{mountpoint}/BIDSIFied_ROOT"

    parts = clean.split("/")
    parent = "/".join(parts[:-1])
    last = parts[-1]
    out_folder = f"BIDSIFied_{last}"
    return f"{mountpoint}/{parent}/{out_folder}" if parent else f"{mountpoint}/{out_folder}"


def compute_input_ref(unc: str, subdir: str) -> str:
    """
    Compute a USER-VISIBLE “reference path” for the input dataset.

    The goal is:
    - Do NOT expose /home/crpn/mnt/... VM internals
    - Show something that resembles the user’s share structure

    Example:
      UNC: //.../crpn$/USers/user_dir
      subdir: sub_a/sub_b/dataset
      -> Users/user_dir/sub_a/sub_b/dataset
    """
    base = _extract_users_base_from_unc(unc)
    s = (subdir or "").strip().replace("\\", "/").strip("/")
    return f"{base}/{s}" if s else base


def compute_output_ref(unc: str, subdir: str) -> str:
    """
    USER-VISIBLE “reference path” for the output folder.

    Example:
      subdir: sub_a/sub_b/dataset
      -> Users/user_dir/sub_a/sub_b/BIDSIFied_dataset

    If subdir is empty:
      -> Users/user_dir/BIDSIFied_ROOT
    """
    base = _extract_users_base_from_unc(unc)
    s = (subdir or "").strip().replace("\\", "/").strip("/")
    if not s:
        return f"{base}/BIDSIFied_ROOT"

    parts = s.split("/")
    parent = "/".join(parts[:-1])
    last = parts[-1]
    out_folder = f"BIDSIFied_{last}"
    return f"{base}/{parent}/{out_folder}" if parent else f"{base}/{out_folder}"


def _extract_users_base_from_unc(unc: str) -> str:
    """
    Extract the "tail" of the UNC starting at "Users" (case-insensitive).
    If "Users" isn't found, fallback to last 2 segments.

    This is a best-effort way to produce a friendly path like:
      Users/<username>/<something>

    Example:
      unc = //server/share/crpn$/USers/norma
      -> Users/norma
    """
    u = (unc or "").strip().replace("\\", "/").strip("/")
    parts = u.split("/")

    idx = None
    for i, p in enumerate(parts):
        if p.lower() == "users":
            idx = i
            break

    tail = parts[idx:] if idx is not None else parts[-2:]
    # Normalize 'USers' -> 'Users'
    if tail and tail[0].lower() == "users":
        tail[0] = "Users"
    return "/".join(tail)


def insert_mount_success(
    *,
    mount_id: str,
    username: str,
    unc: str,
    subdir: str,
    input_path: str,
    output_path: str,
    input_ref: str,
    output_ref: str,
    status: str = "queued",
) -> None:
    """
    Insert a new mount job into the DB.
    Typically called after mount succeeds in /submit.

    Default status is "queued" because the worker will pick it up later.
    """
    with get_conn() as con:
        con.execute("""
            INSERT INTO mounts (
                mount_id, created_at,
                username, unc, subdir,
                input_path, output_path,
                input_ref, output_ref,
                status, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            mount_id,
            now_iso(),
            username,
            unc,
            subdir,
            input_path,
            output_path,
            input_ref,
            output_ref,
            status,
        ))
        con.commit()


def update_mount_status(mount_id: str, status: str, error: Optional[str] = None) -> None:
    """
    Update job status (and optionally error) for a given mount_id.
    """
    with get_conn() as con:
        con.execute("""
            UPDATE mounts
            SET status = ?, error = ?
            WHERE mount_id = ?
        """, (status, error, mount_id))
        con.commit()


def list_mounts(limit: int = 50):
    """
    Return recent jobs for the UI.

    IMPORTANT: Returns ONLY user-visible fields.
    We intentionally do not return internal VM paths.
    """
    with get_conn() as con:
        rows = con.execute("""
            SELECT
              mount_id, created_at, username, unc, subdir,
              input_ref, output_ref,
              status, error
            FROM mounts
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_internal_paths(mount_id: str):
    """
    For the worker later: fetch VM paths without exposing them publicly.
    """
    with get_conn() as con:
        row = con.execute("""
            SELECT input_path, output_path
            FROM mounts
            WHERE mount_id = ?
        """, (mount_id,)).fetchone()
        return dict(row) if row else None

def get_mount_internal(mount_id: str):
    """
    Small helper: fetch mount_id + input_path for a job.
    Looks unused in main.py right now but can be useful for debugging.
    """
    with get_conn() as con:
        row = con.execute("""
            SELECT mount_id, input_path
            FROM mounts
            WHERE mount_id = ?
        """, (mount_id,)).fetchone()
        return dict(row) if row else None



# -----------------------------------------------------------------------------
# Queue mechanism (critical for concurrency!)
# -----------------------------------------------------------------------------


def claim_next_job():
    """
    Atomically claim the next queued job.

    How queueing works:
    - Start "BEGIN IMMEDIATE" transaction: SQLite acquires a RESERVED lock,
      preventing other writers from modifying queued rows simultaneously.
    - Select the oldest queued job.
    - Immediately update it to status='running'
    - Commit

    Result: Even if multiple workers call this at the same time,
    only ONE will successfully claim a given job.
    """
    with get_conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("""
            SELECT mount_id, input_path, output_path
            FROM mounts
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
        """).fetchone()

        if row is None:
            con.execute("COMMIT")
            return None

        con.execute("""
            UPDATE mounts SET status = 'running', error = NULL
            WHERE mount_id = ?
        """, (row["mount_id"],))
        con.execute("COMMIT")
        return dict(row)


def set_job_status(mount_id: str, status: str, error: str | None = None):
    """
    Worker status update (success/failed).
    """
    with get_conn() as con:
        con.execute("""
            UPDATE mounts
            SET status = ?, error = ?
            WHERE mount_id = ?
        """, (status, error, mount_id))
        con.commit()

def append_error(mount_id: str, msg: str) -> None:
    """
    Append a message to the existing error field without overwriting it.

    Useful when cleanup fails: you keep the original BIDSIF failure error
    but also add "Cleanup error: ..."

    Error text is capped at 4000 characters to keep DB/UI manageable.
    """
    msg = (msg or "").strip()
    if not msg:
        return

    with get_conn() as con:
        row = con.execute("SELECT error FROM mounts WHERE mount_id = ?", (mount_id,)).fetchone()
        prev = (row["error"] if row else "") or ""
        combined = (prev + "\n" + msg).strip() if prev else msg

        con.execute("""
            UPDATE mounts
            SET error = ?
            WHERE mount_id = ?
        """, (combined[:4000], mount_id))  # cap length so UI/db stays sane
        con.commit()
