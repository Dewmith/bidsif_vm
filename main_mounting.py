import os
import subprocess
import tempfile
import uuid

# -----------------------------------------------------------------------------
# CIFS Mounting module
#
# Purpose:
# - Mount a user-provided UNC path into the VM filesystem using sudo mount.cifs
# - Validate that the requested subdir exists *inside* the mount
# - Return stable IDs and paths for later processing
#
# Security/robustness choices:
# - Credentials are written to a temp file with 0600 permissions
# - Credentials file is deleted immediately after successful mount
# - Subdir is sanitized to prevent traversal (..)
# - Unique mountpoint per request using a random UUID (mount_id)
# -----------------------------------------------------------------------------


MOUNT_BASE = "/home/crpn/mnt"
MOUNT_OPTS_BASE = "uid=1000,gid=1000,forceuid,forcegid,vers=3.0"
DOMAIN = "salsa"

def run(cmd):
    """Run a command and raise on failure (subprocess.run(check=True))."""
    subprocess.run(cmd, check=True)


def subdir_exists_and_is_dir(path: str) -> bool:
    """Return True if path exists and is a directory."""
    return os.path.exists(path) and os.path.isdir(path)

def sanitize_subdir(s: str) -> str:
    """
    Normalize and validate a subdirectory string.

    - Converts backslashes to slashes
    - Strips leading slash
    - Removes empty and '.' segments
    - Rejects any '..' segment to prevent directory traversal
    """
    s = (s or "").strip().replace("\\", "/").lstrip("/")
    if s == "" or s == ".":
        return ""

    parts = [p for p in s.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("Subdirectory must not contain '..'")
    return "/".join(parts)

def build_access_path(base: str, subdir: str) -> str:
    """
    Build the final internal path the worker will use:
      base mountpoint + cleaned subdir (if any)
    """
    return os.path.join(base, subdir) if subdir else base

def generate_mount_id() -> str:
    """
    Create a stable random ID suitable for a DB primary key.
    """
    return uuid.uuid4().hex  # 32 hex chars

def make_mountpoint_from_id(mount_id: str) -> str:
    """
    Create a mountpoint directory derived from mount_id.

    Example:
      mount_id = abc123...
      mountpoint = /home/crpn/mnt/cifs_abc123...

    This makes mount_id the source of truth (nice for tracking/cleanup).
    """
    os.makedirs(MOUNT_BASE, exist_ok=True)
    mountpoint = os.path.join(MOUNT_BASE, f"cifs_{mount_id}")
    os.makedirs(mountpoint, exist_ok=False)  # fail if collision (extremely unlikely)
    return mountpoint

def mount_dataset(unc: str, username: str, password: str, subdir: str):
    """
    Mount a CIFS share into the VM and validate a requested subdirectory.

    Returns:
      (mount_id, mountpoint, access_path)

    - mount_id: stable ID saved in DB
    - mountpoint: /home/crpn/mnt/cifs_<id>
    - access_path: mountpoint/<subdir> (or mountpoint if subdir empty)

    Raises:
      ValueError, FileNotFoundError, subprocess.CalledProcessError, ...
    """
    UNC = (unc or "").strip()
    username = (username or "").strip()
    password = password or ""
    domain = DOMAIN  # <-- use constant

    if not UNC:
        raise ValueError("UNC is required")
    if not username:
        raise ValueError("Username is required")
    if not password:
        raise ValueError("Password is required")

    # (2) NEW: create DB-safe id + mountpoint from that id
    mount_id = generate_mount_id()
    mountpoint = make_mountpoint_from_id(mount_id)

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    fd, cred_path = tempfile.mkstemp(prefix="cifs_", dir=runtime_dir)
    os.close(fd)

    mounted = False
    try:
        with open(cred_path, "w", encoding="utf-8") as f:
            f.write(f"username={username}\n")
            f.write(f"password={password}\n")
            if domain:
                f.write(f"domain={domain}\n")

        os.chmod(cred_path, 0o600)
        opts = f"credentials={cred_path},{MOUNT_OPTS_BASE}"

        run(["sudo", "mount", "-t", "cifs", UNC, mountpoint, "-o", opts])
        mounted = True

        # delete creds immediately after successful mount
        run(["sudo", "rm", "-f", cred_path])

        subdir_clean = sanitize_subdir(subdir)
        access_path = build_access_path(mountpoint, subdir_clean)

        # validate subdir once
        if subdir_clean != "" and not subdir_exists_and_is_dir(access_path):
            raise FileNotFoundError(f"Not found (or not a directory): {access_path}")

        # (3) NEW: return mount_id too (store this in DB)
        return mount_id, mountpoint, access_path

    except Exception:
        # best-effort cleanup creds
        try:
            run(["sudo", "rm", "-f", cred_path])
        except Exception:
            pass

        # if mounted, unmount
        if mounted:
            try:
                run(["sudo", "umount", mountpoint])
            except Exception:
                pass

        # remove mountpoint dir
        try:
            os.rmdir(mountpoint)
        except Exception:
            pass

        raise

def unmount_and_cleanup(mountpoint: str):
    """
    Unmount and delete mountpoint directory.
    Called after job finishes (success or fail).
    """
    try:
        run(["sudo", "umount", mountpoint])
    except Exception:
        pass
    try:
        os.rmdir(mountpoint)
    except Exception:
        pass