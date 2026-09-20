import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp()
os.environ["TURING_DB"] = os.path.join(_tmp, "test.db")
os.environ["TURING_TODAY"] = "2026-09-20"
