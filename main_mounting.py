import os, subprocess, tempfile
from getpass import getpass

MOUNTPOINT = "/home/crpn/mnt/crpn"
MOUNT_OPTS_BASE = "uid=1000,gid=1000,forceuid,forcegid,vers=3.0"

def run(cmd):
    subprocess.run(cmd, check=True)

def sanitize_subdir(s: str) -> str:
    # Trim spaces and remove leading slashes so os.path.join behaves as expected
    s = s.strip().lstrip("/").replace("\\", "/")
    # Block path traversal (keeps things sane and safe)
    if s == "" or s == ".":
        return ""
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ValueError("Subdirectory must not contain '..'")
    return "/".join(parts)

def main():
    UNC = input("Enter the UNC path to mount (e.g., //smb-crpn-isi-stj.univ-amu.fr/crpn$/USers/user_dir): ").strip()
    username = input("Username: ").strip()
    domain = "salsa"
    password = getpass("Password: ")

    # NEW: ask where the dataset is inside the mounted share
    subdir_input = input("Dataset subdirectory inside the share (e.g., folder_1/dataset, leave empty for root): ")
    subdir = sanitize_subdir(subdir_input)

    os.makedirs(MOUNTPOINT, exist_ok=True)

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    fd, cred_path = tempfile.mkstemp(prefix="cifs_", dir=runtime_dir)
    os.close(fd)

    try:
        with open(cred_path, "w", encoding="utf-8") as f:
            f.write(f"username={username}\n")
            f.write(f"password={password}\n")
            if domain:
                f.write(f"domain={domain}\n")

        os.chmod(cred_path, 0o600)

        opts = f"credentials={cred_path},{MOUNT_OPTS_BASE}"
        run(["sudo", "mount", "-t", "cifs", UNC, MOUNTPOINT, "-o", opts])

        run(["sudo", "rm", "-f", cred_path])

        access_path = os.path.join(MOUNTPOINT, subdir) if subdir else MOUNTPOINT
        print("Mounted successfully. Credentials file removed.")
        print("Access your directory at:")
        print(access_path)

    except Exception:
        try:
            run(["sudo", "rm", "-f", cred_path])
        except Exception:
            pass
        raise

if __name__ == "__main__":
    main()
