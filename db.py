# db.py
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bidsif.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_conn() -> sqlite3.Connection:
    # sqlite is safe for a small local VM app like this
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
                access_path TEXT NOT NULL,
                output_path TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS idx_mounts_created_at ON mounts(created_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mounts_username ON mounts(username)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_mounts_status ON mounts(status)")
        con.commit()


def compute_folder_id_from_subdir(subdir: str) -> str:
    """
    Your rule: last path component of subdir.
    If subdir is empty -> ROOT.
    """
    s = (subdir or "").strip().replace("\\", "/").strip("/")
    return os.path.basename(s) if s else "ROOT"


def compute_output_path(mountpoint: str, subdir: str) -> str:
    """
    output path rule:
      output_dir = mountpoint + "/BIDSIFied_" + last(subdir)
    Example:
      subdir="Dewmith_W" -> ".../BIDSIFied_Dewmith_W"
    """
    folder_id = compute_folder_id_from_subdir(subdir)
    out_dirname = f"BIDSIFied_{folder_id}"
    return str(Path(mountpoint) / out_dirname)


def insert_mount_success(
    *,
    mount_id: str,
    username: str,
    unc: str,
    subdir: str,
    access_path: str,
    output_path: str,
    status: str = "mounted",
) -> None:
    with get_conn() as con:
        con.execute("""
            INSERT INTO mounts (
                mount_id, created_at, username, unc, subdir, access_path, output_path, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            mount_id,
            now_iso(),
            username,
            unc,
            subdir,
            access_path,
            output_path,
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
    with get_conn() as con:
        rows = con.execute("""
            SELECT mount_id, created_at, username, unc, subdir, access_path, output_path, status, error
            FROM mounts
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]
