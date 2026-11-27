import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from common import parse_args, verdict

workspace, answer, fixture = parse_args()
original = (fixture / "workspace" / "test_order.py").read_bytes()
current = workspace / "test_order.py"
if not current.exists() or hashlib.sha256(current.read_bytes()).digest() != hashlib.sha256(original).digest():
    verdict(False, "test_order.py was changed or deleted")
with tempfile.TemporaryDirectory() as tmp:
    for name in ("order.py", "test_order.py"):
        if not (workspace / name).exists():
            verdict(False, f"{name} is missing")
        shutil.copy(workspace / name, Path(tmp) / name)
    shutil.copy(fixture / "workspace" / "test_order.py", Path(tmp) / "test_order.py")
    proc = subprocess.run(["python3", "-m", "unittest", "-v"], cwd=tmp, capture_output=True, text=True, timeout=60)
if proc.returncode != 0:
    verdict(False, "unit tests fail", output=proc.stderr[-600:])
verdict(True, "tests pass and test_order.py is unchanged")
