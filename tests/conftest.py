"""Make the backend package importable from the repo root.

Without this, `from workers.heavy_toolpath import ...` fails: tests/ sits beside
backend/, not inside it. The previous suite documented `pytest tests/test_suite.py`
as the way to run it, but the only two tests that touched real product code
errored on import — so they never actually ran.
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
