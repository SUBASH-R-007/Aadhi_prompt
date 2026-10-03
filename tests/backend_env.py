"""Shared setup for the backend tests: one throwaway database and storage folder, configured
before anything imports the app, so the real projects.db, exports/ and assets/ are never used.

Import this module first in every backend test module.
"""
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="aadhi-backend-test-")
atexit.register(lambda: (database.engine.dispose(), shutil.rmtree(TMP, ignore_errors=True)))
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db").replace("\\", "/")
os.environ["EXPORTS_DIR"] = os.path.join(TMP, "exports")
os.environ["ASSETS_DIR"] = os.path.join(TMP, "assets")
os.environ["JWT_SECRET"] = "backend-tests-secret-0123456789abcdef"
os.environ["AI_GENERATION_ENABLED"] = "0"  # tests never call an AI media provider
os.environ["AI_RECOVERY_ENABLED"] = "0"    # no background recovery loop: recovery tests run its sweeps themselves
os.environ["MANIM_SANDBOX_DIR"] = os.path.join(TMP, "manim-sandbox")  # sandbox workspaces of the tests, thrown away
os.chdir(REPO)  # server.py mounts static folders relative to the working directory
sys.path.insert(0, REPO)

import database  # noqa: E402
import models  # noqa: E402
import server  # noqa: E402

FFMPEG = shutil.which("ffmpeg")


def assert_isolated():
    """Refuse to run against anything but the throwaway database."""
    db_file = os.path.normcase(os.path.abspath(database.engine.url.database or ""))
    assert db_file.startswith(os.path.normcase(os.path.abspath(TMP))), database.engine.url


def ensure_user(name):
    db = database.SessionLocal()
    try:
        user = db.query(models.User).filter(models.User.username == name).first()
        if not user:
            user = models.User(username=name, password_hash="unused")
            db.add(user)
            db.commit()
        return user.id
    finally:
        db.close()


def auth(user):
    return {"Authorization": "Bearer " + server.create_access_token({"sub": user})}


def ffmpeg(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)
