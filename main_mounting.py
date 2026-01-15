import os
import subprocess
import tempfile
from getpass import getpass

# Base directory under which we create a unique mountpoint each run
MOUNT_BASE = "/home/crpn/mnt"

MOUNT_OPTS_BASE = "uid=1000,gid=1000,forceuid,forcegid,vers=3.0"

def run(cmd):
    """Run a command and raise on failure."""
    subprocess.run(cmd, check=True)

def sanitize_subdir(s: str) -> str:
    """
    Normalize a user-provided subpath inside the mounted share.

    - Removes leading slashes so os.path.join(base, subdir) behaves correctly.
    - Converts backslashes to forward slashes (Windows-style pastes).
    - Blocks '..' traversal for safety.
    """
    s = s.strip().replace("\\", "/").lstrip("/")
    if s == "" or s == ".":
        return ""

    parts = [p for p in s.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise ValueError("Subdirectory must not contain '..'")
    return "/".join(parts)

def build_access_path(base: str, subdir: str) -> str:
    """Return the full path to the intended dataset directory."""
    return os.path.join(base, subdir) if subdir else base

def subdir_exists_and_is_dir(path: str) -> bool:
    return os.path.exists(path) and os.path.isdir(path)

def prompt_for_subdir_until_valid(base: str) -> str:
    """
    Ask user for a dataset subdirectory (relative to mountpoint) and keep asking
    until it exists (or user leaves empty to use the share root).
    """
    while True:
        raw = input(
            "Dataset subdirectory inside the share (e.g., folder_1/dataset). "
            "Leave empty for root: "
        )
        try:
            subdir = sanitize_subdir(raw)
        except ValueError as e:
            print(f"Invalid subdirectory: {e}")
            continue

        access_path = build_access_path(base, subdir)

        # If user chose root, accept immediately (root will exist after mount).
        if subdir == "":
            return subdir

        if subdir_exists_and_is_dir(access_path):
            return subdir

        print(f"Not found (or not a directory): {access_path}")
        print("Please re-enter the dataset subdirectory.")

def make_unique_mountpoint() -> str:
    """
    Create a unique directory under MOUNT_BASE for this run.
    Example: /home/crpn/mnt/cifs_ab12cd34
    """
    os.makedirs(MOUNT_BASE, exist_ok=True)
    return tempfile.mkdtemp(prefix="cifs_", dir=MOUNT_BASE)

def main():
    UNC = input(
        "Enter the UNC path to mount (e.g., //smb-crpn-isi-stj.univ-amu.fr/crpn$/USers/user_dir): "
    ).strip()

    username = input("Username: ").strip()
    domain = "salsa"
    password = getpass("Password: ")

    # NEW: unique mountpoint for this session
    mountpoint = make_unique_mountpoint()
    print(f"Using mountpoint: {mountpoint}")

    # Prefer RAM-backed runtime dir if available; fall back to /tmp
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

        # Mount share
        run(["sudo", "mount", "-t", "cifs", UNC, mountpoint, "-o", opts])
        mounted = True

        # Remove creds after successful mount
        run(["sudo", "rm", "-f", cred_path])

        # Now that the filesystem is mounted, prompt until a valid subdir is provided
        subdir = prompt_for_subdir_until_valid(mountpoint)
        access_path = build_access_path(mountpoint, subdir)

        print("Mounted successfully. Credentials file removed.")
        print("Access your directory at:")
        print(access_path)

        # Do your work here (or call another function) using access_path

    except Exception:
        # Best-effort cleanup of creds
        try:
            run(["sudo", "rm", "-f", cred_path])
        except Exception:
            pass

        # If mount succeeded but later logic failed, unmount to avoid lingering mounts
        if mounted:
            try:
                run(["sudo", "umount", mountpoint])
            except Exception:
                pass

        # Best-effort cleanup of mountpoint directory (only works if not mounted)
        try:
            os.rmdir(mountpoint)
        except Exception:
            pass

        raise

if __name__ == "__main__":
    main()
