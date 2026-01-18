import os
import subprocess
import tempfile

MOUNT_BASE = "/home/crpn/mnt"
MOUNT_OPTS_BASE = "uid=1000,gid=1000,forceuid,forcegid,vers=3.0"
DOMAIN = "salsa"

def run(cmd):
    """Run a command and raise on failure."""
    subprocess.run(cmd, check=True)

def sanitize_subdir(s: str) -> str:
    s = (s or "").strip().replace("\\", "/").lstrip("/")
    if s == "" or s == ".":
        return ""

    parts = [p for p in s.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("Subdirectory must not contain '..'")
    return "/".join(parts)

def build_access_path(base: str, subdir: str) -> str:
    return os.path.join(base, subdir) if subdir else base

def make_unique_mountpoint() -> str:
    os.makedirs(MOUNT_BASE, exist_ok=True)
    return tempfile.mkdtemp(prefix="cifs_", dir=MOUNT_BASE)

def mount_dataset(unc: str, username: str, password: str, subdir: str) -> tuple[str, str]:
    """
    Mount the CIFS share to a unique mountpoint, validate subdir exists, and return:
      (mountpoint, access_path)

    Raises Exception on any failure. Caller should record failure and let user resubmit.
    """
    UNC = (unc or "").strip()
    username = (username or "").strip()
    password = password or ""
    subdir = sanitize_subdir(subdir)

    if not UNC:
        raise ValueError("UNC path is required")
    if not username:
        raise ValueError("Username is required")
    if password == "":
        raise ValueError("Password is required")

    mountpoint = make_unique_mountpoint()

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    fd, cred_path = tempfile.mkstemp(prefix="cifs_", dir=runtime_dir)
    os.close(fd)

    mounted = False
    try:
        with open(cred_path, "w", encoding="utf-8") as f:
            f.write(f"username={username}\n")
            f.write(f"password={password}\n")
            if DOMAIN:
                f.write(f"domain={DOMAIN}\n")

        os.chmod(cred_path, 0o600)

        opts = f"credentials={cred_path},{MOUNT_OPTS_BASE}"

        # Mount share
        run(["sudo", "mount", "-t", "cifs", UNC, mountpoint, "-o", opts])
        mounted = True

        # Remove creds after successful mount
        run(["sudo", "rm", "-f", cred_path])

        # Validate subdir once (no prompting)
        access_path = build_access_path(mountpoint, subdir)
        if subdir != "" and not (os.path.exists(access_path) and os.path.isdir(access_path)):
            raise FileNotFoundError(f"Not found (or not a directory): {access_path}")

        return mountpoint, access_path

    except Exception:
        # Best-effort cleanup of creds
        try:
            run(["sudo", "rm", "-f", cred_path])
        except Exception:
            pass

        # If mount succeeded but later logic failed, unmount
        if mounted:
            try:
                run(["sudo", "umount", mountpoint])
            except Exception:
                pass

        # Best-effort cleanup of mountpoint directory
        try:
            os.rmdir(mountpoint)
        except Exception:
            pass

        raise

def unmount_and_cleanup(mountpoint: str):
    """Always try to unmount and remove the mountpoint directory."""
    try:
        run(["sudo", "umount", mountpoint])
    except Exception:
        pass
    try:
        os.rmdir(mountpoint)
    except Exception:
        pass
