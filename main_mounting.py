import os, subprocess, tempfile
from getpass import getpass

MOUNTPOINT = "/home/crpn/mnt/crpn"
MOUNT_OPTS_BASE = "uid=1000,gid=1000,forceuid,forcegid,vers=3.0"

def run(cmd):
    subprocess.run(cmd, check=True)

def main():
    UNC = input("Enter the UNC path to mount (e.g., smb://smb-crpn-isi-stj.univ-amu.fr/crpn$/USers/directoryname): ").strip()
    username = input("Username: ").strip()
    domain = "salsa"
    password = getpass("Password: ")

    os.makedirs(MOUNTPOINT, exist_ok=True)

    # Prefer RAM-backed runtime dir if available; fall back to /tmp
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
        # NOTE: This mount itself must be run as root (sudo), or it will fail later.
        run(["sudo", "mount", "-t", "cifs", UNC, MOUNTPOINT, "-o", opts])

        # Remove creds after successful mount
        run(["sudo", "rm", "-f", cred_path])

        print("Mounted successfully. Credentials file removed.")
    except Exception:
        try:
            run(["sudo", "rm", "-f", cred_path])
        except Exception:
            pass
        raise

if __name__ == "__main__":
    main()
