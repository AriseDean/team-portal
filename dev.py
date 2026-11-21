#!/usr/bin/env python3
"""One-command local setup for trying the portal on your own computer (Windows, macOS, Linux).

    python dev.py            set everything up (first run) and start the site
    python dev.py demo       same, but also fill it with fake demo teams and reports
    python dev.py test       run the automated tests
    python dev.py reset      delete the local database and uploads (start fresh)

Needs only Python 3.10+. It creates a private virtual environment in .venv/ and keeps all local data in data/.
Nothing here is used in production (Docker has its own setup).
"""
import hashlib
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
DATA = ROOT / "data"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
HOST, PORT = "127.0.0.1", os.environ.get("PORT", "8000")
ADMIN_USER, ADMIN_PASS = "admin", "admin12345"  # local testing only


def run(*args, check=True, env=None):
    return subprocess.run([str(PY), *args], cwd=ROOT, check=check, env=env)


def ensure_venv():
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10 or newer is required (found %s)." % sys.version.split()[0])
    if not PY.exists():
        print("→ Creating virtual environment (.venv) …")
        venv.EnvBuilder(with_pip=True).create(VENV)
    wanted = hashlib.sha256((ROOT / "requirements.txt").read_bytes()).hexdigest()
    marker = VENV / ".requirements-hash"
    if not marker.exists() or marker.read_text() != wanted:
        print("→ Installing dependencies …")
        run("-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", "requirements.txt")
        marker.write_text(wanted)


def dev_env():
    env = dict(os.environ)
    env.update(
        DEBUG="1",
        DATA_DIR=str(DATA),
        ALLOWED_HOSTS="localhost,127.0.0.1",
        ADMIN_USERNAME=ADMIN_USER,
        ADMIN_PASSWORD=ADMIN_PASS,
        # Emails are printed in this terminal instead of being sent.
        EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
    )
    env.pop("SECRET_KEY", None)  # DEBUG mode supplies a throw-away key
    return env


def prepare(env):
    ensure_venv()
    print("→ Preparing the database …")
    run("manage.py", "migrate", "--noinput", "-v", "0", env=env)
    run("manage.py", "bootstrap", env=env)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return
    if cmd == "reset":
        if DATA.exists():
            shutil.rmtree(DATA)
        print("Local data deleted. Run `python dev.py` to start fresh.")
        return

    env = dev_env()
    if cmd == "test":
        ensure_venv()
        run("manage.py", "test", "portal", env=env)
        return
    if cmd not in ("run", "demo"):
        sys.exit(f"Unknown command {cmd!r}. Try: python dev.py --help")

    prepare(env)
    if cmd == "demo":
        run("manage.py", "seed_demo", env=env)

    print(f"""
──────────────────────────────────────────────────────────────
  Student site : http://{HOST}:{PORT}/
  Admin        : http://{HOST}:{PORT}/admin/    login: {ADMIN_USER} / {ADMIN_PASS}
  Emails       : printed in this terminal (nothing is really sent)
  Stop         : Ctrl+C        Start over: python dev.py reset
──────────────────────────────────────────────────────────────
""")
    try:
        run("manage.py", "runserver", f"{HOST}:{PORT}", env=env, check=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
