"""
Cleanup helper for run_scenario.sh.

Kills the normal_traffic.py / attack_simulation.py processes started inside
the victim container. This used to be a `docker exec victim pkill -f ...`
call, but python:3.11-slim has no procps (no pkill binary).

IMPORTANT: this is run as a file (`python3 /ephemeralguard/cleanup.py`),
not as inline code via `python3 -c "..."`. If the target filenames were
passed as a literal string on the command line, this process's own
cmdline would contain the substring "attack_simulation" and it would
self-trigger the EphemeralGuard - Cryptomining Process Pattern Falco rule
every time cleanup ran. Keeping the logic in a file instead means the
cmdline Falco sees is just the path to this script.
"""

import os
import signal

TARGETS = ("normal_traffic.py", "attack_simulation.py")

for pid in os.listdir("/proc"):
    if not pid.isdigit():
        continue
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmdline = f.read().decode(errors="ignore")
        if any(t in cmdline for t in TARGETS):
            os.kill(int(pid), signal.SIGTERM)
    except Exception:
        pass
