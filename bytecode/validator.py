"""
flux_bridge.bytecode.validator — constraint validation for flux-core bytecode.

Usage::

    from flux_bridge.bytecode import validate

    result = validate(b"\\x80")   # HALT-only
    if result.safe:
        print("OK")
    else:
        for err in result.errors:
            print(f"ERROR: {err}")
"""

from __future__ import annotations

import dataclasses
from typing import Sequence

from flux_bridge.bytecode.opcodes import (
    Op,
    from_byte,
    read_reg,
    read_i16,
    read_u16,
    instruction_size,
    is_a2a,
    is_control_flow,
)


# ── Result type ────────────────────────────────────────────────────── #

@dataclasses.dataclass
class ValidationResult:
    """Outcome of a bytecode validation pass.

    Attributes
    ----------
    safe : bool
        *True* when no errors were found (warnings do not affect safety).
    errors : list[str]
        Fatal constraint violations that would prevent execution.
    warnings : list[str]
        Non-fatal concerns (performance, style, etc.).
    """

    safe: bool
    errors: list[str]
    warnings: list[str]

    def __bool__(self) -> bool:
        return self.safe

    def __repr__(self) -> str:
        status = "PASS" if self.safe else "FAIL"
        return (
            f"ValidationResult({status}, "
            f"errors={len(self.errors)}, warnings={len(self.warnings)})"
        )


# ── helpers ────────────────────────────────────────────────────────── #

_MAX_GP_REG: int = 15
_MAX_INSTR_WARN: int = 1000


# ── public validator ───────────────────────────────────────────────── #

def validate(bytecode: bytes) -> ValidationResult:
    """Run all validation passes against *bytecode*.

    Checks performed:

    * All bytes decode to valid opcodes.
    * Register operands are in 0-15 range (GP registers only).
    * Instructions are properly aligned (no partial reads).
    * A ``HALT`` instruction is present.
    * Cycle budget warning if >1000 instructions.
    * A2A payloads (TELL, ASK) are not empty.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(bytecode, (bytes, bytearray)):
        errors.append("bytecode must be bytes")
        return ValidationResult(safe=False, errors=errors, warnings=warnings)

    if len(bytecode) == 0:
        errors.append("bytecode is empty")
        return ValidationResult(safe=False, errors=errors, warnings=warnings)

    pc = 0
    length = len(bytecode)
    instr_count = 0
    has_halt = False

    while pc < length:
        offset = pc
        op_byte = bytecode[pc]
        op = from_byte(op_byte)
        instr_count += 1

        if op is None:
            errors.append(
                f"offset 0x{offset:04X}: invalid opcode 0x{op_byte:02X}"
            )
            pc += 1
            continue

        pc += 1  # consume opcode byte

        # ── Variable-length A2A ── #
        if is_a2a(op):
            if pc + 1 >= length:
                errors.append(
                    f"offset 0x{offset:04X}: {op.name} truncated "
                    f"(missing payload length field)"
                )
                continue

            payload_len = bytecode[pc] | (bytecode[pc + 1] << 8)
            pc += 2

            if payload_len == 0:
                if op in (Op.TELL, Op.ASK):
                    warnings.append(
                        f"offset 0x{offset:04X}: {op.name} has empty payload"
                    )
            elif payload_len > 0xFFFF:
                errors.append(
                    f"offset 0x{offset:04X}: {op.name} payload length "
                    f"{payload_len} exceeds maximum (65535)"
                )

            available = length - pc
            if payload_len > available:
                # Check if there are trailing bytes (partial payload)
                partial = available
                errors.append(
                    f"offset 0x{offset:04X}: {op.name} payload truncated: "
                    f"expected {payload_len} bytes, got {partial}"
                )
                pc = length  # consume everything
            else:
                pc += payload_len
            continue

        # ── Expected fixed size ── #
        try:
            expected_total = instruction_size(op)
        except ValueError:
            errors.append(
                f"offset 0x{offset:04X}: cannot determine size for {op.name}"
            )
            continue

        remaining = length - pc
        needed = expected_total - 1  # already consumed opcode

        if remaining < needed:
            errors.append(
                f"offset 0x{offset:04X}: {op.name} truncated — "
                f"needs {needed} more byte(s), have {remaining}"
            )
            pc = length  # consume what's left
            continue

            # ── Register bounds check ── #
        # Only validate bytes that are actually registers, not immediates/padding
        _validate_regs_for_op(bytecode, pc, offset, op, expected_total, errors)

        # ── Consume operand bytes ── #
        pc += needed

        # ── Check for HALT ── #
        if op == Op.HALT:
            has_halt = True

    # ── Post-loop checks ── #
    if not has_halt:
        errors.append("bytecode must contain a HALT instruction")

    if instr_count > _MAX_INSTR_WARN:
        warnings.append(
            f"large bytecode — {instr_count} instructions "
            f"(> {_MAX_INSTR_WARN} = slow path)"
        )

    safe = len(errors) == 0
    return ValidationResult(safe=safe, errors=errors, warnings=warnings)


# ── internal helpers ───────────────────────────────────────────────── #

# Instruction formats (after opcode byte):
#   Format A (2 byte):  reg
#   Format B (3 byte):  reg, reg
#   Format C (4 byte):  reg, i16
#   Format D (4 byte):  pad, i16
#   Format E (3 byte):  pad, pad

_OPS_REG_AT_PC0: frozenset = frozenset({
    # 2-byte: op + reg
    Op.INC, Op.DEC, Op.INEG, Op.INOT, Op.PUSH, Op.POP,
    # 3-byte: op + reg + reg
    Op.MOV,
    Op.IADD, Op.ISUB, Op.IMUL, Op.IDIV, Op.IMOD,
    Op.IAND, Op.IOR, Op.IXOR, Op.ISHL, Op.ISHR,
    Op.CMP,
    Op.FADD, Op.FSUB, Op.FMUL, Op.FDIV,
    Op.LOAD, Op.STORE,
    # 4-byte: op + reg + i16
    Op.MOVI, Op.JZ, Op.JNZ,
})

_OPS_REG_AT_PC1: frozenset = frozenset({
    # 3-byte: op + reg + reg  (byte[pc+1] is second register)
    Op.MOV,
    Op.IADD, Op.ISUB, Op.IMUL, Op.IDIV, Op.IMOD,
    Op.IAND, Op.IOR, Op.IXOR, Op.ISHL, Op.ISHR,
    Op.CMP,
    Op.FADD, Op.FSUB, Op.FMUL, Op.FDIV,
    Op.LOAD, Op.STORE,
})

# Ops that have NO registers in their operand bytes (padding/immediates only)
_OPS_NO_REGS: frozenset = frozenset({Op.RET, Op.JMP, Op.CALL})


def _validate_regs_for_op(
    bytecode: bytes,
    pc: int,
    instr_offset: int,
    op: Op,
    expected_total: int,
    errors: list[str],
) -> None:
    """Validate register bytes that are actually registers for this op."""
    # Check pc+0 if it's a register
    if op in _OPS_REG_AT_PC0:
        _validate_reg_at(bytecode, pc, instr_offset, errors, "operand")

    # Check pc+1 if it's a register (second register in 3-byte reg+reg ops)
    if op in _OPS_REG_AT_PC1:
        _validate_reg_at(bytecode, pc + 1, instr_offset, errors, "second operand")


def _validate_reg_at(
    bytecode: bytes,
    pos: int,
    instr_offset: int,
    errors: list[str],
    context: str = "register",
) -> None:
    """Check that the byte at *pos* is a valid GP register (0-15)."""
    if pos >= len(bytecode):
        return  # already reported as truncated
    r = bytecode[pos]
    if r > _MAX_GP_REG:
        errors.append(
            f"offset 0x{instr_offset:04X}: {context} R{r} exceeds "
            f"GP range 0-{_MAX_GP_REG} (FP registers 16-31 reserved)"
        )


def _check_a2a_payload(
    bytecode: bytes,
    offset: int,
    pc: int,
    errors: list[str],
    warnings: list[str],
) -> tuple[int, int]:
    """Helper to validate an A2A instruction's payload.

    Returns ``(new_pc, payload_len)``.
    """
    payload_len = bytecode[pc] | (bytecode[pc + 1] << 8)
    return pc + 2, payload_len
