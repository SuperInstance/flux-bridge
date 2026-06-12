#!/usr/bin/env python3
"""
Performance benchmarks for the flux_bridge VM.

Phase 3 deliverable — measures bytecode generation, execution throughput,
memory usage, and pure-Python vs native (Rust) speed comparison.

Usage:
    python3 -m flux_bridge.tests.performance_benchmark
    python3 -c "from flux_bridge.tests.performance_benchmark import *; run_benchmarks()"
"""

from __future__ import annotations

import math
import os
import sys
import time
import tracemalloc
from statistics import mean, stdev
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flux_bridge.bytecode.assembler import Assembler
from flux_bridge.vm_harness import PythonInterpreter, VMHarness

# ---------------------------------------------------------------------------
# 1.  generate_test_bytecode
# ---------------------------------------------------------------------------

def generate_test_bytecode(size: int = 1000) -> bytes:
    """Generate a bytecode program of approximately *size* instructions.

    The program computes a triangular sum::

        R0 = size / 2       # loop counter start
        R1 = size           # limit
        R2 = 0              # accumulator
    loop:
        IADD R2, R0         # accumulate
        DEC R0              # decrement counter
        JNZ R0, loop        # loop if counter != 0
        HALT

    The actual size may vary slightly depending on *size* due to the
    loop structure, but is roughly proportional.

    Args:
        size: Target number of instructions to generate.

    Returns:
        Compiled bytecode bytes.
    """
    asm = Assembler()
    asm.emit_mov_i(0, size // 2)    # 4 bytes — loop counter
    asm.emit_mov_i(1, size)         # 4 bytes — limit
    asm.emit_mov_i(2, 0)            # 4 bytes — accumulator
    asm.label("loop")
    asm.emit_add(2, 0)             # 3 bytes — accumulate
    asm.emit_dec(0)                 # 2 bytes — decrement
    asm.emit_jnz(0, "loop")        # 4 bytes — conditional branch
    asm.emit_halt()                  # 1 byte  — stop

    return asm.assemble()


# ---------------------------------------------------------------------------
# 2.  benchmark_execution_time
# ---------------------------------------------------------------------------

def benchmark_execution_time(repetitions: int = 100) -> dict[str, Any]:
    """Measure VM execution throughput.

    Runs a triangular-sum bytecode program (size=1000) for *repetitions*
    iterations and returns timing statistics.

    Args:
        repetitions: Number of executions to measure.

    Returns:
        Dict with keys::

            {
                "repetitions": int,
                "mean_s": float,       # mean wall-clock time per execution
                "min_s": float,
                "max_s": float,
                "std_s": float,        # population standard deviation
                "total_s": float,      # total wall time for all reps
                "instructions_per_sec": float,  # throughput estimate
                "cycles_per_run": int,
            }
    """
    bc = generate_test_bytecode(1000)
    vm = PythonInterpreter()

    # Warm up
    vm.execute(bc)

    times: list[float] = []
    cycles_per_run: int = 0

    for _ in range(repetitions):
        vm.reset()
        t0 = time.perf_counter()
        result = vm.execute(bc)
        t1 = time.perf_counter()
        times.append(t1 - t0)
        if result.success:
            cycles_per_run = result.cycles_used

    avg = mean(times)
    s = stdev(times) if len(times) > 1 else 0.0
    ips = cycles_per_run / avg if avg > 0 else 0.0

    return {
        "repetitions": repetitions,
        "mean_s": round(avg, 8),
        "min_s": round(min(times), 8),
        "max_s": round(max(times), 8),
        "std_s": round(s, 8),
        "total_s": round(sum(times), 6),
        "instructions_per_sec": round(ips),
        "cycles_per_run": cycles_per_run,
        "bytecode_bytes": len(bc),
    }


# ---------------------------------------------------------------------------
# 3.  benchmark_memory_usage
# ---------------------------------------------------------------------------

def benchmark_memory_usage(iterations: int = 1000) -> dict[str, Any]:
    """Measure peak memory usage during bytecode execution.

    Uses Python's ``tracemalloc`` module to snapshot memory before and
    after executing a bytecode program *iterations* times.

    Args:
        iterations: Number of executions to measure.

    Returns:
        Dict with keys::

            {
                "iterations": int,
                "baseline_bytes": int,     # memory before first execution
                "peak_bytes": int,         # peak memory during execution
                "peak_delta_bytes": int,   # peak - baseline
                "per_execution_bytes": int, # estimated per-execution overhead
            }
    """
    bc = generate_test_bytecode(1000)
    vm = PythonInterpreter()
    vm.execute(bc)  # warm up

    # Start tracemalloc and get baseline
    tracemalloc.start()
    _, baseline = tracemalloc.get_traced_memory()
    peak = baseline

    for _ in range(iterations):
        vm.reset()
        # Execute inside tracemalloc tracking
        snapshot_before = tracemalloc.take_snapshot()
        result = vm.execute(bc)
        snapshot_after = tracemalloc.take_snapshot()
        _, current_peak = tracemalloc.get_traced_memory()
        if current_peak > peak:
            peak = current_peak

    # Stop tracking
    tracemalloc.stop()

    delta = peak - baseline
    per_exec = delta // iterations if iterations > 0 else 0

    return {
        "iterations": iterations,
        "baseline_bytes": baseline,
        "peak_bytes": peak,
        "peak_delta_bytes": delta,
        "per_execution_bytes": per_exec,
    }


# ---------------------------------------------------------------------------
# 4.  compare_python_vs_native
# ---------------------------------------------------------------------------

def compare_python_vs_native(trials: int = 50) -> dict[str, Any]:
    """Benchmark Python vs native Rust execution throughput.

    Runs the triangular-sum program repeatedly and collects timing
    statistics for both the Python interpreter and, if available, the
    native ``flux-vm`` binary.

    Args:
        trials: Number of timing samples per implementation.

    Returns:
        Dict with ``"python"`` and ``"native"`` sub-dicts, each
        containing ``mean_s``, ``std_s``, ``min_s``, ``max_s``,
        ``cycles_per_sec``, and ``available`` (bool).

        If the native binary is unavailable, ``native.available`` is
        ``False`` and no timing fields are present.
    """
    bc = generate_test_bytecode(1000)
    py_vm = PythonInterpreter()
    py_times: list[float] = []

    # Warm up
    py_vm.execute(bc)

    # ── Python timing ────────────────────────────────────────────────
    for _ in range(trials):
        py_vm.reset()
        t0 = time.perf_counter()
        result = py_vm.execute(bc)
        t1 = time.perf_counter()
        py_times.append(t1 - t0)

    py_mean = mean(py_times)
    py_std = stdev(py_times) if len(py_times) > 1 else 0.0
    py_cycles = result.cycles_used if result.success else 0

    # ── Native timing (if available) ─────────────────────────────────
    native_times: list[float] = []
    native_ok = False
    native_cycles = 0

    harness = VMHarness(release=True)
    binary = harness.build(release=True)

    if binary and os.path.isfile(binary):
        native_ok = True
        env = os.environ.copy()
        env["MAX_CYCLES"] = str(harness.max_cycles)

        for _ in range(trials):
            t0 = time.perf_counter()
            native_result = harness.execute(bc, max_cycles=10_000_000)
            t1 = time.perf_counter()
            native_times.append(t1 - t0)
            if native_result.success:
                native_cycles = native_result.cycles_used

    # ── Build result dict ─────────────────────────────────────────────
    result: dict[str, Any] = {
        "python": {
            "available": True,
            "trials": trials,
            "mean_s": round(py_mean, 8),
            "std_s": round(py_std, 8),
            "min_s": round(min(py_times), 8),
            "max_s": round(max(py_times), 8),
            "cycles_per_sec": round(py_cycles / py_mean, 0) if py_mean > 0 else 0,
            "cycles_per_run": py_cycles,
        }
    }

    if native_ok and native_times:
        nat_mean = mean(native_times)
        nat_std = stdev(native_times) if len(native_times) > 1 else 0.0
        speedup = py_mean / nat_mean if nat_mean > 0 else float("inf")

        result["native"] = {
            "available": True,
            "trials": trials,
            "mean_s": round(nat_mean, 8),
            "std_s": round(nat_std, 8),
            "min_s": round(min(native_times), 8),
            "max_s": round(max(native_times), 8),
            "cycles_per_sec": round(native_cycles / nat_mean, 0) if nat_mean > 0 else 0,
            "cycles_per_run": native_cycles,
            "speedup": round(speedup, 2),
        }
    else:
        result["native"] = {
            "available": False,
        }

    return result


# ---------------------------------------------------------------------------
# 5.  Main — Run all benchmarks, print formatted table
# ---------------------------------------------------------------------------

def _fmt_time(seconds: float) -> str:
    """Format a time value for table output."""
    if seconds < 1e-6:
        return f"{seconds * 1e9:.2f} ns"
    elif seconds < 1e-3:
        return f"{seconds * 1e6:.2f} µs"
    elif seconds < 1.0:
        return f"{seconds * 1e3:.2f} ms"
    else:
        return f"{seconds:.4f} s"


def _fmt_memory(bytes_val: int) -> str:
    """Format a memory value for table output."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    else:
        return f"{bytes_val / (1024 * 1024):.2f} MB"


def run_benchmarks() -> dict[str, Any]:
    """Execute all benchmarks and print a formatted results table.

    Returns the combined results dict.
    """
    print("╔══════════════════════════════════════════════════════════════╗")
    print("║       flux_bridge  —  Performance Benchmarks               ║")
    print("╚══════════════════════════════════════════════════════════════╝")
    print()

    results: dict[str, Any] = {}

    # ── Benchmark 1: Execution Time ──────────────────────────────────
    print("─" * 60)
    print("  [1] Execution Throughput  (1000-instruction loop)")
    print()
    exec_times = benchmark_execution_time(repetitions=100)
    results["execution_time"] = exec_times
    print(f"    Repetitions:          {exec_times['repetitions']}")
    print(f"    Bytecode size:        {exec_times['bytecode_bytes']} B")
    print(f"    Cycles per run:       {exec_times['cycles_per_run']:,}")
    print(f"    Mean time:            {_fmt_time(exec_times['mean_s'])}")
    print(f"    Min time:             {_fmt_time(exec_times['min_s'])}")
    print(f"    Max time:             {_fmt_time(exec_times['max_s'])}")
    print(f"    Std dev:              {_fmt_time(exec_times['std_s'])}")
    print(f"    Total time:           {_fmt_time(exec_times['total_s'])}")
    print(f"    Throughput:           {exec_times['instructions_per_sec']:>12,} instr/s")
    print()

    # ── Benchmark 2: Memory Usage ────────────────────────────────────
    print("─" * 60)
    print("  [2] Memory Usage  (tracemalloc)")
    print()
    mem = benchmark_memory_usage(iterations=1000)
    results["memory_usage"] = mem
    print(f"    Iterations:           {mem['iterations']}")
    print(f"    Baseline memory:      {_fmt_memory(mem['baseline_bytes'])}")
    print(f"    Peak memory:          {_fmt_memory(mem['peak_bytes'])}")
    print(f"    Peak delta:           {_fmt_memory(mem['peak_delta_bytes'])}")
    print(f"    Per-execution (est):  {_fmt_memory(mem['per_execution_bytes'])}")
    print()

    # ── Benchmark 3: Python vs Native ────────────────────────────────
    print("─" * 60)
    print("  [3] Python vs Native Comparison  (50 trials)")
    print()
    comparison = compare_python_vs_native(trials=50)
    results["comparison"] = comparison

    py = comparison["python"]
    native = comparison["native"]

    # Table header
    print(f"    {'Metric':<28} {'Python':>15} {'Native':>15} {'Speedup':>10}")
    print(f"    {'─'*28} {'─'*15} {'─'*15} {'─'*10}")

    format_row = lambda label, py_val, nat_val, sp: (
        f"    {label:<28} {str(py_val):>15} {str(nat_val):>15} {str(sp):>10}"
    )

    py_mean = _fmt_time(py["mean_s"])
    nat_mean = _fmt_time(native["mean_s"]) if native.get("available") else "N/A"
    speedup = f"{native['speedup']}x" if native.get("available") and native.get("speedup", 0) < float("inf") else "N/A"
    print(format_row("Mean time", py_mean, nat_mean, speedup))

    py_std = _fmt_time(py["std_s"])
    nat_std = _fmt_time(native["std_s"]) if native.get("available") else "N/A"
    print(format_row("Std dev", py_std, nat_std, ""))

    py_min = _fmt_time(py["min_s"])
    nat_min = _fmt_time(native["min_s"]) if native.get("available") else "N/A"
    print(format_row("Min time", py_min, nat_min, ""))

    py_max = _fmt_time(py["max_s"])
    nat_max = _fmt_time(native["max_s"]) if native.get("available") else "N/A"
    print(format_row("Max time", py_max, nat_max, ""))

    py_cps = f"{py['cycles_per_sec']:,.0f} cyc/s"
    nat_cps = f"{native['cycles_per_sec']:,.0f} cyc/s" if native.get("available") else "N/A"
    print(format_row("Throughput", py_cps, nat_cps, ""))

    if native.get("available"):
        print(f"\n    Native binary: ✅ available ({'release' if harness.release else 'debug'} mode)")
    else:
        print(f"\n    Native binary: ❌ unavailable (Rust toolchain or build required)")

    print()
    print("─" * 60)
    print("  Benchmark complete.")
    print()

    return results


def main() -> int:
    """CLI entry point. Runs all benchmarks and returns exit code (0)."""
    run_benchmarks()
    return 0


if __name__ == "__main__":
    sys.exit(main())
