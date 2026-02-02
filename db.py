# db.py
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bidsif.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
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
    subdir: sub_a/sub_b/dataset
    output: mountpoint/sub_a/sub_b/BIDSIFied_dataset
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
    User-visible path for the input dataset.
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
    User-visible output reference path.
    subdir: sub_a/sub_b/dataset
    -> Users/user_dir/sub_a/sub_b/BIDSIFied_dataset
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
    Extract the UNC tail starting at Users (case-insensitive),
    fallback to last 2 segments.
    Returns e.g. 'USers/user_dir' (we normalize to 'Users/...')
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
    status: str = "mounted and awaiting execution",
) -> None:
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
    with get_conn() as con:
        con.execute("""
            UPDATE mounts
            SET status = ?, error = ?
            WHERE mount_id = ?
        """, (status, error, mount_id))
        con.commit()


def list_mounts(limit: int = 50):
    """
    IMPORTANT: This returns ONLY user-visible fields.
    No VM paths in the API output.
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
    with get_conn() as con:
        row = con.execute("""
            SELECT mount_id, input_path
            FROM mounts
            WHERE mount_id = ?
        """, (mount_id,)).fetchone()
        return dict(row) if row else None


def claim_next_job():
    with get_conn() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("""
            SELECT mount_id, input_path, output_path
            FROM mounts
            WHERE status = 'mounted and awaiting execution'
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
    with get_conn() as con:
        con.execute("""
            UPDATE mounts
            SET status = ?, error = ?
            WHERE mount_id = ?
        """, (status, error, mount_id))
        con.commit()
