#!/usr/bin/env python3
"""
Assembler + Disassembler tests for flux_bridge.

Tests programmatic bytecode construction, label resolution,
disassembler roundtrip, and invalid register detection.

Usage:
    python3 -c "from flux_bridge.tests.test_assembler import *"
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flux_bridge.bytecode.assembler import Assembler
from flux_bridge.bytecode.disassembler import Disassembler

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
# 1.  emit_mov_i — basic MOVI encoding
# ---------------------------------------------------------------------------

def test_emit_mov_i_basic():
    """MOVI R0, 42 encodes as 0x2B 0x00 0x2A 0x00"""
    asm = Assembler()
    asm.emit_mov_i(0, 42)
    asm.emit_halt()
    bc = asm.assemble()

    check(
        "MOVI + HALT is 5 bytes",
        len(bc) == 5,
        f"got {len(bc)} bytes",
    )
    check(
        "MOVI opcode = 0x2B",
        bc[0] == 0x2B,
        f"got 0x{bc[0]:02X}",
    )
    check(
        "MOVI register = 0x00 (R0)",
        bc[1] == 0x00,
        f"got 0x{bc[1]:02X}",
    )
    check(
        "MOVI immediate = 42 (LE)",
        bc[2] == 42 and bc[3] == 0x00,
        f"got bytes [{bc[2]}, {bc[3]}]",
    )
    check(
        "HALT opcode = 0x80",
        bc[4] == 0x80,
        f"got 0x{bc[4]:02X}",
    )

    # Also test hex output
    hex_str = asm.assemble_to_hex()
    check(
        "assemble_to_hex matches",
        hex_str == "2b002a0080",
        f"got {hex_str!r}",
    )


# ---------------------------------------------------------------------------
# 2.  label resolution — forward jump label
# ---------------------------------------------------------------------------

def test_label_resolution():
    """Forward label on JZ resolves to correct offset"""
    asm = Assembler()
    asm.emit_mov_i(0, 0)       # 4 bytes
    asm.emit_jz(0, "skip")     # 4 bytes (fixup to resolve)
    asm.emit_mov_i(1, 99)      # 4 bytes (skipped if R0==0)
    asm.label("skip")
    asm.emit_mov_i(2, 42)      # 4 bytes (land here)
    asm.emit_halt()             # 1 byte
    bc = asm.assemble()

    # Layout:
    # 0x00 MOVI R0, 0   (4B)  → 0x00-0x03
    # 0x04 JZ R0, skip  (4B)  → 0x04-0x07, skip=0x0C, instr_end=0x08
    #                              offset = 0x0C - 0x08 = +4
    # 0x08 MOVI R1, 99  (4B)
    # 0x0C MOVI R2, 42  (4B)  ← skip target
    # 0x10 HALT          (1B)

    check(
        "forward label: bytecode is 17 bytes",
        len(bc) == 17,
        f"got {len(bc)} bytes",
    )

    # JZ at offset 4: opcode(0x05), reg(0x00), offset(0x04, 0x00)
    check(
        "JZ opcode = 0x05",
        bc[4] == 0x05,
        f"got 0x{bc[4]:02X}",
    )
    check(
        "JZ register = 0x00 (R0)",
        bc[5] == 0x00,
    )
    check(
        "JZ offset = +4 (skip lands on MOVI R2,42)",
        bc[6] == 0x04 and bc[7] == 0x00,
        f"got offset bytes [{bc[6]}, {bc[7]}]",
    )

    # MOVI R2, 42 should be at offset 0x0C
    check(
        "MOVI at skip target @ offset 0x0C",
        bc[12] == 0x2B and bc[13] == 0x02 and bc[14] == 42,
        f"got bytes at 0x0C: [{bc[12]}, {bc[13]}, {bc[14]}]",
    )

    # Verify via disassembly
    instrs = Disassembler.disassemble(bc)
    check(
        "disassembly produces instructions",
        len(instrs) >= 4,
        f"got {len(instrs)} instructions",
    )
    check(
        "last instruction is HALT",
        instrs[-1].text == "HALT",
        f"got {instrs[-1].text!r}",
    )


# ---------------------------------------------------------------------------
# 3.  disassembler roundtrip — assemble → disassemble → match text
# ---------------------------------------------------------------------------

def test_disassembler_roundtrip():
    """Assemble a program, disassemble it, verify instruction text matches"""
    asm = Assembler()
    asm.emit_mov_i(0, 10)
    asm.emit_mov_i(1, 20)
    asm.emit_add(0, 1)       # R0 = 10 + 20 = 30
    asm.emit_sub(1, 0)       # R1 = 20 - 30 = -10
    asm.emit_mul(2, 0)       # R2 = 30 * 30 = 900
    asm.emit_inc(2)          # R2 = 901
    asm.emit_dec(2)          # R2 = 900
    asm.emit_push(0)         # push R0 (30)
    asm.emit_pop(3)          # pop -> R3 = 30
    asm.emit_ineg(1)         # R1 = -(-10) = 10
    asm.emit_cmp(0, 3)       # compare R0(30) vs R3(30)
    asm.emit_ret()
    asm.emit_halt()

    bc = asm.assemble()
    instrs = Disassembler.disassemble(bc)

    # Build expected instruction texts
    expected_texts = [
        "MOVI R0, 10",
        "MOVI R1, 20",
        "IADD R0, R1",
        "ISUB R1, R0",
        "IMUL R2, R0",
        "INC R2",
        "DEC R2",
        "PUSH R0",
        "POP R3",
        "INEG R1",
        "CMP R0, R3",
        "RET",
        "HALT",
    ]

    check(
        "roundtrip produces correct number of instructions",
        len(instrs) == len(expected_texts),
        f"got {len(instrs)}, expected {len(expected_texts)}",
    )

    for i, (inst, expected) in enumerate(zip(instrs, expected_texts)):
        check(
            f"instr[{i}] text = {expected}",
            inst.text == expected,
            f"got {inst.text!r}",
        )

    # All instructions should have correct sizes
    expected_sizes = [4, 4, 3, 3, 3, 2, 2, 2, 2, 2, 3, 3, 1]
    total_bytes = sum(expected_sizes)
    check(
        f"total bytecode size = {total_bytes}",
        len(bc) == total_bytes,
        f"got {len(bc)} bytes, expected {total_bytes}",
    )


# ---------------------------------------------------------------------------
# 4.  invalid register — R16 for GP should error
# ---------------------------------------------------------------------------

def test_invalid_register():
    """R16 (outside 0-15 GP range) raises ValueError in emit helpers"""
    test_cases = [
        ("emit_mov_i", lambda a: a.emit_mov_i(16, 0)),
        ("emit_mov (dst)", lambda a: a.emit_mov(16, 0)),
        ("emit_mov (src)", lambda a: a.emit_mov(0, 20)),
        ("emit_add (dst)", lambda a: a.emit_add(16, 0)),
        ("emit_add (src)", lambda a: a.emit_add(0, 20)),
        ("emit_sub (dst)", lambda a: a.emit_sub(16, 0)),
        ("emit_mul (dst)", lambda a: a.emit_mul(16, 0)),
        ("emit_div (dst)", lambda a: a.emit_div(16, 0)),
        ("emit_mod (dst)", lambda a: a.emit_mod(16, 0)),
        ("emit_inc", lambda a: a.emit_inc(16)),
        ("emit_dec", lambda a: a.emit_dec(16)),
        ("emit_ineg", lambda a: a.emit_ineg(16)),
        ("emit_inot", lambda a: a.emit_inot(16)),
        ("emit_push", lambda a: a.emit_push(16)),
        ("emit_pop", lambda a: a.emit_pop(16)),
        ("emit_jz", lambda a: a.emit_jz(16, "foo")),
        ("emit_jnz", lambda a: a.emit_jnz(16, "foo")),
        ("emit_and (dst)", lambda a: a.emit_and(16, 0)),
        ("emit_or (dst)", lambda a: a.emit_or(16, 0)),
        ("emit_xor (dst)", lambda a: a.emit_xor(16, 0)),
        ("emit_shl (dst)", lambda a: a.emit_shl(16, 0)),
        ("emit_shr (dst)", lambda a: a.emit_shr(16, 0)),
        ("emit_cmp (dst)", lambda a: a.emit_cmp(16, 0)),
    ]

    all_passed = True
    for name, fn in test_cases:
        try:
            a = Assembler()
            fn(a)
            check(f"{name} — raises ValueError? No", False)
            all_passed = False
        except ValueError:
            check(f"{name} — raises ValueError", True)
        except Exception as e:
            check(f"{name} — raises ValueError? No ({type(e).__name__})", False)
            all_passed = False

    if all_passed:
        print("  [All register bounds checks passed]")


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

TEST_FUNCTIONS = [
    test_emit_mov_i_basic,
    test_label_resolution,
    test_disassembler_roundtrip,
    test_invalid_register,
]

print("\n=== Assembler Tests ===")
for fn in TEST_FUNCTIONS:
    fn()

print(f"\nResults: {PASS} passed, {FAIL} failed")
if FAIL:
    print("Some tests FAILED")
