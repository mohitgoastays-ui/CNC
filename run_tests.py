#!/usr/bin/env python3
"""
Relief Studio — run the full test suite.

    python run_tests.py            # everything available
    python run_tests.py --browser  # only the JavaScript algorithm tests
    python run_tests.py --server   # only the Python worker tests

Two suites, because the product is two codebases:

  tests/test_browser_algorithms.mjs   extracts the real @core-start/@core-end
                                      region from phase3-relief-studio.html and
                                      runs it under Node. Needs node.

  tests/test_suite.py                 imports the real worker modules.
                                      Needs numpy + pytest.

A missing runtime is reported as SKIPPED, never counted as a pass. The point of
this file is that the totals it prints are trustworthy.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd, label):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}", flush=True)
    try:
        return subprocess.call(cmd, cwd=ROOT)
    except FileNotFoundError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--server", action="store_true")
    args = ap.parse_args()
    both = not (args.browser or args.server)

    results = {}

    if both or args.browser:
        if shutil.which("node") is None:
            results["browser"] = ("SKIPPED", "node not on PATH")
        else:
            rc = run(["node", "tests/test_browser_algorithms.mjs"],
                     "Browser algorithms (real shipped JavaScript)")
            results["browser"] = ("PASS" if rc == 0 else "FAIL", f"exit {rc}")

    if both or args.server:
        try:
            import numpy  # noqa: F401
            import pytest  # noqa: F401
            have = True
        except ImportError as e:
            have = False
            why = str(e)
        if not have:
            results["server"] = ("SKIPPED", f"{why} — pip install numpy pytest")
        else:
            rc = run([sys.executable, "-m", "pytest", "tests/test_suite.py", "-q"],
                     "Server workers (real worker modules)")
            results["server"] = ("PASS" if rc == 0 else "FAIL", f"exit {rc}")

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for name, (status, detail) in results.items():
        print(f"  {name:<10} {status:<8} {detail}")

    if any(s == "FAIL" for s, _ in results.values()):
        return 1
    if any(s == "SKIPPED" for s, _ in results.values()):
        print("\nSome suites were skipped — this is NOT a full pass.")
        return 2
    print("\nAll suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
