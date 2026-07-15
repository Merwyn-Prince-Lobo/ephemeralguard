"""
EphemeralGuard - Module 4
Benchmarking — validates sub-100ms capture window
"""

import os
import sys
import time
import statistics
import concurrent.futures

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "module3"))
from audit_logger import log_audit_event

TARGET_MS = 100
RUNS      = 20
PARALLEL  = 10


def single_run(i):
    start = time.perf_counter()
    log_audit_event(
        event_type="BENCHMARK_RUN",
        function_name=f"bench-lambda-{i}",
        metadata={"run": i, "test": "latency"}
    )
    return (time.perf_counter() - start) * 1000


def run_benchmark():
    print("=" * 55)
    print("  EphemeralGuard Module 4 - Benchmark")
    print("=" * 55)

    print(f"\n[1] Single-threaded latency ({RUNS} runs)...")
    times = []
    for i in range(RUNS):
        ms = single_run(i)
        times.append(ms)
        status = "✓" if ms < TARGET_MS else "✗"
        print(f"    Run {i+1:02d}: {ms:6.1f}ms  [{status}]")

    passed = sum(1 for t in times if t < TARGET_MS)
    print(f"\n    Min    : {min(times):.1f}ms")
    print(f"    Max    : {max(times):.1f}ms")
    print(f"    Mean   : {statistics.mean(times):.1f}ms")
    print(f"    Median : {statistics.median(times):.1f}ms")
    print(f"    Stdev  : {statistics.stdev(times):.1f}ms")
    print(f"    Target : <{TARGET_MS}ms")
    print(f"    Passed : {passed}/{RUNS} ({(passed/RUNS)*100:.0f}%)")

    print(f"\n[2] Parallel stress test ({PARALLEL} concurrent events)...")
    start = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=PARALLEL) as ex:
        futures  = [ex.submit(single_run, i + RUNS) for i in range(PARALLEL)]
        p_times  = [f.result() for f in concurrent.futures.as_completed(futures)]
    total_ms = (time.perf_counter() - start) * 1000

    p_passed = sum(1 for t in p_times if t < TARGET_MS)
    print(f"    Total wall time : {total_ms:.1f}ms")
    print(f"    Mean per event  : {statistics.mean(p_times):.1f}ms")
    print(f"    Under {TARGET_MS}ms       : {p_passed}/{PARALLEL}")

    print("\n── Verdict ──────────────────────────────────────────")
    overall = (passed / RUNS) * 100
    if overall >= 90:
        print(f"  ✅ PASS — {overall:.0f}% of events captured within {TARGET_MS}ms")
        print(f"     EphemeralGuard meets the race-against-termination requirement")
    else:
        print(f"  ❌ FAIL — only {overall:.0f}% within {TARGET_MS}ms")
        print(f"     Optimization needed before production deploy")


if __name__ == "__main__":
    run_benchmark()