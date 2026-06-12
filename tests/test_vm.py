#!/usr/bin/env python3
"""
VM tests for flux_bridge.

Tests the PythonInterpreter against the Rust flux-core interpreter semantics.

Each test manually constructs bytecode (matching the Rust assembler
encoding) and asserts expected register/memory/error state.

Usage:
    python3 -c "from flux_bridge.tests.test_vm import *"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flux_bridge.vm_harness import (
    PythonInterpreter,
    VMResult,
    DivisionByZero,
    InvalidOpcode,
)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# ---------------------------------------------------------------------------
# 1.  MOVI — Load immediate into register
# ---------------------------------------------------------------------------

def test_movi_r0_42():
    """MOVI R0, 42 → regs[0] == 42"""
    vm = PythonInterpreter()
    # MOVI R0, 42 = 0x2B 0x00 0x2A 0x00 ; HALT = 0x80
    bc = bytes([0x2B, 0x00, 0x2A, 0x00, 0x80])
    result = vm.execute(bc)
    check(
        "MOVI R0, 42 sets R0 to 42",
        result.success and result.registers[0] == 42,
        f"got R0={result.registers[0]}",
    )


# ---------------------------------------------------------------------------
# 2.  IADD — Add two registers
# ---------------------------------------------------------------------------

def test_add_two_numbers():
    """R0=3, R1=4, IADD → R0=7"""
    vm = PythonInterpreter()
    # MOVI R0, 3 = 0x2B 0x00 0x03 0x00
    # MOVI R1, 4 = 0x2B 0x01 0x04 0x00
    # IADD R0, R1 = 0x08 0x00 0x01
    # HALT = 0x80
    bc = bytes([
        0x2B, 0x00, 0x03, 0x00,   # MOVI R0, 3
        0x2B, 0x01, 0x04, 0x00,   # MOVI R1, 4
        0x08, 0x00, 0x01,         # IADD R0, R1
        0x80,                      # HALT
    ])
    result = vm.execute(bc)
    check(
        "IADD R0, R1 (3+4=7)",
        result.success and result.registers[0] == 7,
        f"got R0={result.registers[0]} (expected 7)",
    )
    # Also check flag_zero is False (result != 0)
    check(
        "flag_zero is False after non-zero result",
        not result.flag_zero,
    )


# ---------------------------------------------------------------------------
# 3.  JZ — Jump if register is zero
# ---------------------------------------------------------------------------

def test_jump_zero():
    """R0=0, JZ → jumps over HALT, lands on MOVI"""
    vm = PythonInterpreter()
    # Layout:
    # 0x00 MOVI R0, 0    (4B, 0x00-0x03)
    # 0x04 JZ R0, +1     (4B, 0x04-0x07)  — skip forward 1 byte past HALT
    # 0x08 HALT           (1B, 0x08)        — skipped
    # 0x09 MOVI R1, 99   (4B, 0x09-0x0C)   — land here
    # 0x0D HALT           (1B, 0x0D)
    # After consuming JZ, pc=0x08. Jump +1 → pc=0x09 (MOVI R1, 99).
    bc = bytes([
        0x2B, 0x00, 0x00, 0x00,   # MOVI R0, 0
        0x05, 0x00, 0x01, 0x00,   # JZ R0, +1  (skip past HALT at 0x08)
        0x80,                      # HALT (skipped)
        0x2B, 0x01, 0x63, 0x00,   # MOVI R1, 99
        0x80,                      # HALT
    ])
    result = vm.execute(bc)
    check(
        "JZ with R0=0 jumps past HALT",
        result.success and result.registers[1] == 99,
        f"got R1={result.registers[1]} (expected 99), R0={result.registers[0]}",
    )


# ---------------------------------------------------------------------------
# 4.  IDIV — Division by zero
# ---------------------------------------------------------------------------

def test_division_by_zero():
    """IDIV with R0 / R1 where R1 == 0 → error"""
    vm = PythonInterpreter()
    # MOVI R0, 10 = 0x2B 0x00 0x0A 0x00
    # MOVI R1, 0  = 0x2B 0x01 0x00 0x00
    # IDIV R0, R1 = 0x0B 0x00 0x01
    # HALT        = 0x80
    bc = bytes([
        0x2B, 0x00, 0x0A, 0x00,   # MOVI R0, 10
        0x2B, 0x01, 0x00, 0x00,   # MOVI R1, 0
        0x0B, 0x00, 0x01,         # IDIV R0, R1
        0x80,
    ])
    result = vm.execute(bc)
    check(
        "IDIV by zero returns DivisionByZero error",
        not result.success and result.error is not None,
        f"got success={result.success}, error={result.error!r}",
    )
    check(
        "error string mentions 'division' or 'zero'",
        result.error is not None and (
            "division" in result.error.lower() or "zero" in result.error.lower()
        ),
        f"error={result.error!r}",
    )


# ---------------------------------------------------------------------------
# 5.  HALT — Execution stops immediately
# ---------------------------------------------------------------------------

def test_halt():
    """HALT stops execution; subsequent instructions are not run"""
    vm = PythonInterpreter()
    # MOVI R0, 5  = 0x2B 0x00 0x05 0x00
    # HALT        = 0x80
    # MOVI R1, 99 = 0x2B 0x01 0x63 0x00  (should NOT execute)
    bc = bytes([
        0x2B, 0x00, 0x05, 0x00,   # MOVI R0, 5
        0x80,                      # HALT
        0x2B, 0x01, 0x63, 0x00,   # MOVI R1, 99 (skipped)
    ])
    result = vm.execute(bc)
    check(
        "HALT stops execution",
        result.halted,
        f"halted={result.halted}",
    )
    check(
        "R0 is 5 (before HALT)",
        result.registers[0] == 5,
        f"R0={result.registers[0]}",
    )
    check(
        "R1 is 0 (instruction after HALT not executed)",
        result.registers[1] == 0,
        f"R1={result.registers[1]} (expected 0)",
    )
    check(
        "HALT reports success",
        result.success,
    )


# ---------------------------------------------------------------------------
# 6.  Invalid opcode
# ---------------------------------------------------------------------------

def test_invalid_opcode():
    """0xFF → InvalidOpcode error"""
    vm = PythonInterpreter()
    bc = bytes([0xFF, 0x80])  # invalid opcode, then HALT (never reached)
    result = vm.execute(bc)
    check(
        "Invalid opcode 0xFF returns error",
        not result.success and result.error is not None,
        f"got success={result.success}, error={result.error!r}",
    )
    check(
        "error string mentions 'opcode' or '0xFF'",
        result.error is not None and (
            "opcode" in result.error.lower() or "ff" in result.error.lower()
        ),
        f"error={result.error!r}",
    )


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Module-level discovery for `python3 -c` usage
    pass

# Auto-run when imported as module or exec'd
TEST_FUNCTIONS = [
    test_movi_r0_42,
    test_add_two_numbers,
    test_jump_zero,
    test_division_by_zero,
    test_halt,
    test_invalid_opcode,
]

if __name__ != "__main__":
    # Only auto-run when not imported interactively
    pass

# Manual invocation via exec
print("\n=== VM Tests ===")
for fn in TEST_FUNCTIONS:
    fn()

print(f"\nResults: {PASS} passed, {FAIL} failed")
if FAIL:
    print("Some tests FAILED")
