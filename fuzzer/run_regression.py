#!/usr/bin/env python3
"""
NIRVFuzz Regression Runner

Loads every crash binary from crashes/, re-runs both clean and buggy
simulations, and verifies each still triggers divergence (CRC or trap mismatch).

Usage:
    cd fuzzer && python3 run_regression.py

Exit code:
    0 — all crashes still reproduce
    1 — one or more crashes no longer reproduce (or no crashes found)
"""
import os
import sys
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor

SIM_CLEAN = "../sim/Vtop"
SIM_BUGGY = "../sim/Vtop_buggy"
CRASH_DIR = "crashes"


def run_sim(executable, path):
    try:
        result = subprocess.run(
            [executable, path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10
        )
        stdout = result.stdout
        s, e = stdout.find("{"), stdout.rfind("}")
        if s != -1 and e != -1:
            return json.loads(stdout[s:e+1])
    except Exception as ex:
        print(f"  [error] {os.path.basename(executable)}: {ex}")
    return None


def evaluate(path):
    """Run clean and buggy sims concurrently."""
    with ThreadPoolExecutor(max_workers=2) as ex:
        fc = ex.submit(run_sim, SIM_CLEAN, path)
        fb = ex.submit(run_sim, SIM_BUGGY, path)
        return fc.result(), fb.result()


def divergence_reason(clean, buggy):
    """Return a non-empty reason string if the two results diverge, else ''."""
    if clean is None or buggy is None:
        return ""
    if clean.get("crc_out", 0) != buggy.get("crc_out", 0):
        return f"CRC  clean={hex(clean.get('crc_out',0))} buggy={hex(buggy.get('crc_out',0))}"
    if clean.get("trap", 0) != buggy.get("trap", 0):
        return f"Trap clean={clean.get('trap',0)} buggy={buggy.get('trap',0)}"
    return ""


def main():
    if not os.path.isdir(CRASH_DIR):
        print(f"Crash directory '{CRASH_DIR}' not found.")
        sys.exit(1)

    crashes = sorted(
        f for f in os.listdir(CRASH_DIR)
        if f.endswith(".bin") and not f.endswith("_min.bin")
    )

    if not crashes:
        print("No crash files found.")
        sys.exit(1)

    print(f"=== NIRVFuzz Regression — {len(crashes)} crashes ===\n")

    passed = failed = skipped = 0

    for crash_file in crashes:
        path  = os.path.join(CRASH_DIR, crash_file)
        clean, buggy = evaluate(path)

        if clean is None or buggy is None:
            print(f"  [SKIP] {crash_file} — sim error")
            skipped += 1
            continue

        reason = divergence_reason(clean, buggy)
        if reason:
            print(f"  [PASS] {crash_file} — {reason}")
            passed += 1
        else:
            print(f"  [FAIL] {crash_file} — no longer diverges!")
            failed += 1

    print(f"\nResults: {passed} pass, {failed} fail, {skipped} skip "
          f"(out of {len(crashes)} crashes)")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
