#!/usr/bin/env python3
"""Run a pytest target and report peak resident set size (RSS) for the test process."""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time


def _read_rss_kb(pid: int) -> int:
    """Return current RSS in kilobytes for a process id."""
    try:
        with open(f"/proc/{pid}/status", "r", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except OSError:
        return 0
    return 0


def measure_peak_rss(pytest_args: list[str]) -> int:
    """Run pytest in a child process and return peak RSS in megabytes."""
    command = [sys.executable, "-m", "pytest", *pytest_args]
    process = subprocess.Popen(command)
    peak_kb = 0
    stop = threading.Event()

    def poll_rss() -> None:
        nonlocal peak_kb
        while not stop.is_set():
            peak_kb = max(peak_kb, _read_rss_kb(process.pid))
            if process.poll() is not None:
                break
            time.sleep(0.5)

    poller = threading.Thread(target=poll_rss, daemon=True)
    poller.start()
    exit_code = process.wait()
    stop.set()
    poller.join(timeout=2.0)
    peak_kb = max(peak_kb, _read_rss_kb(process.pid))
    peak_mb = peak_kb / 1024.0
    print(f"pytest exit={exit_code} peak_rss_mb={peak_mb:.1f}")
    return exit_code


def main() -> None:
    """Parse CLI arguments and run the RSS measurement harness."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pytest_target", nargs=argparse.REMAINDER, help="pytest args after --")
    args = parser.parse_args()
    target = args.pytest_target
    if target and target[0] == "--":
        target = target[1:]
    if not target:
        parser.error("Provide a pytest target, e.g. tests/pipeline/test_idoc.py::TestIDocBuild")
    sys.exit(measure_peak_rss(target))


if __name__ == "__main__":
    main()
