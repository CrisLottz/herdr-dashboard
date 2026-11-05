"""Single entry point for every command, on every OS.

Herdr plugin manifests take one argv per action and reject duplicate ids, so
there is no clean way to declare "run the .cmd on Windows, the shell script on
Linux". Instead every command is `python bootstrap.py <command>`: this file only
uses the standard library, makes sure the private venv exists and matches
requirements.txt, then re-runs the real program (`python -m hdash`) inside it.
"""

import hashlib
import os
import subprocess
import sys
import venv

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
PY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")
REQS = os.path.join(ROOT, "requirements.txt")
STAMP = os.path.join(VENV, ".requirements.sha256")


def _digest():
    with open(REQS, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ensure_venv():
    """Create .venv on first run and reinstall when requirements.txt changes."""
    if not os.path.exists(PY):
        print("herdr-dashboard: creating .venv (first run only)...", flush=True)
        venv.EnvBuilder(with_pip=True).create(VENV)
    try:
        with open(STAMP) as f:
            current = f.read().strip()
    except OSError:
        current = ""
    if current != _digest():
        print("herdr-dashboard: installing dependencies...", flush=True)
        subprocess.check_call([PY, "-m", "pip", "install", "-q", "--disable-pip-version-check", "-r", REQS])
        with open(STAMP, "w") as f:
            f.write(_digest())


def main():
    args = sys.argv[1:]
    try:
        ensure_venv()
    except (OSError, subprocess.CalledProcessError) as exc:
        sys.exit(f"herdr-dashboard: could not prepare the venv ({exc}). "
                 "On Debian/Ubuntu install python3-venv first.")
    if args == ["--install"]:
        return
    env = dict(os.environ, PYTHONPATH=ROOT + os.pathsep + os.environ.get("PYTHONPATH", ""))
    cmd = [PY, "-m", "hdash", *args]
    if os.name == "nt":
        # os.execv on Windows spawns a detached child and breaks the console; wait instead.
        sys.exit(subprocess.call(cmd, env=env))
    os.execve(PY, cmd, env)


if __name__ == "__main__":
    main()
