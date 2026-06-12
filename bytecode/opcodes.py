"""
flux_bridge.bytecode.opcodes — Op enum matching flux-core bytecode/opcodes.rs.

All 38 opcodes defined in the Rust Op enum are mirrored here, plus utility
functions for instruction sizing, control-flow detection, and ALU detection.
"""

from __future__ import annotations

from enum import IntEnum


class Op(IntEnum):
    """Each variant maps to the same u8 discriminant as flux-core's Op enum."""

    # Flow / no-op
    NOP = 0x00
    JMP = 0x04
    JZ = 0x05
    JNZ = 0x06
    CALL = 0x07
    RET = 0x28
    HALT = 0x80
    YIELD = 0x81

    # Register moves / memory
    MOV = 0x01
    LOAD = 0x02    # defined but not yet active in from_byte
    STORE = 0x03   # defined but not yet active in from_byte
    MOVI = 0x2B    # load immediate

    # Integer ALU (2-register, dst/src)
    IADD = 0x08
    ISUB = 0x09
    IMUL = 0x0A
    IDIV = 0x0B
    IMOD = 0x0C

    # Integer unary
    INEG = 0x0D
    INC = 0x0E
    DEC = 0x0F

    # Integer bitwise
    IAND = 0x10
    IOR = 0x11
    IXOR = 0x12
    INOT = 0x13
    ISHL = 0x14
    ISHR = 0x15

    # Comparison
    CMP = 0x2D

    # Stack
    PUSH = 0x20
    POP = 0x21
    DUP = 0x22

    # Float ALU
    FADD = 0x40
    FSUB = 0x41
    FMUL = 0x42
    FDIV = 0x43

    # Agent-to-Agent (A2A)
    TELL = 0x60
    ASK = 0x61
    DELEGATE = 0x62
    BROADCAST = 0x66

    # ------------------------------------------------------------------ #
    # helper methods
    # ------------------------------------------------------------------ #

    def __repr__(self) -> str:
        return f"Op.{self.name}"

    def __str__(self) -> str:
        return self.name


# ── Public helper functions ────────────────────────────────────────── #

_OP_FROM_BYTE: dict[int, Op] = {
    0x00: Op.NOP,
    0x01: Op.MOV,
    0x04: Op.JMP,
    0x05: Op.JZ,
    0x06: Op.JNZ,
    0x07: Op.CALL,
    0x08: Op.IADD,
    0x09: Op.ISUB,
    0x0A: Op.IMUL,
    0x0B: Op.IDIV,
    0x0C: Op.IMOD,
    0x0D: Op.INEG,
    0x0E: Op.INC,
    0x0F: Op.DEC,
    0x10: Op.IAND,
    0x11: Op.IOR,
    0x12: Op.IXOR,
    0x13: Op.INOT,
    0x14: Op.ISHL,
    0x15: Op.ISHR,
    0x20: Op.PUSH,
    0x21: Op.POP,
    0x22: Op.DUP,
    0x28: Op.RET,
    0x2B: Op.MOVI,
    0x2D: Op.CMP,
    0x40: Op.FADD,
    0x41: Op.FSUB,
    0x42: Op.FMUL,
    0x43: Op.FDIV,
    0x60: Op.TELL,
    0x61: Op.ASK,
    0x62: Op.DELEGATE,
    0x66: Op.BROADCAST,
    0x80: Op.HALT,
    0x81: Op.YIELD,
}


def from_byte(byte: int) -> Op | None:
    """Decode a single byte into an Op, or *None* if unknown."""
    return _OP_FROM_BYTE.get(byte)


# Opcode size classification — matches flux-core's Assembler::instr_size
_FIXED_SIZE: dict[Op, int] = {
    Op.NOP: 1,
    Op.DUP: 1,
    Op.HALT: 1,
    Op.YIELD: 1,
    Op.INC: 2,
    Op.DEC: 2,
    Op.INEG: 2,
    Op.INOT: 2,
    Op.PUSH: 2,
    Op.POP: 2,
    Op.MOV: 3,
    Op.LOAD: 3,
    Op.STORE: 3,
    Op.IADD: 3,
    Op.ISUB: 3,
    Op.IMUL: 3,
    Op.IDIV: 3,
    Op.IMOD: 3,
    Op.IAND: 3,
    Op.IOR: 3,
    Op.IXOR: 3,
    Op.ISHL: 3,
    Op.ISHR: 3,
    Op.CMP: 3,
    Op.RET: 3,
    Op.FADD: 3,
    Op.FSUB: 3,
    Op.FMUL: 3,
    Op.FDIV: 3,
    Op.MOVI: 4,
    Op.JMP: 4,
    Op.JZ: 4,
    Op.JNZ: 4,
    Op.CALL: 4,
}

_VARIABLE_OPS: frozenset[Op] = frozenset(
    {Op.TELL, Op.ASK, Op.DELEGATE, Op.BROADCAST}
)

_CONTROL_FLOW: frozenset[Op] = frozenset(
    {Op.JMP, Op.JZ, Op.JNZ, Op.CALL, Op.RET, Op.HALT, Op.YIELD}
)

_ALU_OPS: frozenset[Op] = frozenset(
    {
        Op.IADD, Op.ISUB, Op.IMUL, Op.IDIV, Op.IMOD,
        Op.INEG, Op.INC, Op.DEC,
        Op.IAND, Op.IOR, Op.IXOR, Op.INOT, Op.ISHL, Op.ISHR,
        Op.FADD, Op.FSUB, Op.FMUL, Op.FDIV,
    }
)


def instruction_size(op: Op) -> int:
    """Return the fixed byte-size of *op* (1-4).

    Raises ``ValueError`` for variable-length A2A opcodes
    (TELL, ASK, DELEGATE, BROADCAST) — use ``a2a_payload_length`` instead.
    """
    if op in _VARIABLE_OPS:
        raise ValueError(
            f"{op.name} is variable-length; use a2a_payload_length()"
        )
    return _FIXED_SIZE.get(op, 1)


def is_control_flow(op: Op) -> bool:
    """Return *True* if *op* changes or terminates execution flow."""
    return op in _CONTROL_FLOW


def is_alu(op: Op) -> bool:
    """Return *True* if *op* is an arithmetic or logical operation."""
    return op in _ALU_OPS


def is_a2a(op: Op) -> bool:
    """Return *True* if *op* is an agent-to-agent instruction."""
    return op in _VARIABLE_OPS


# ── Instruction field reading helpers ──────────────────────────────── #

def read_reg(bytecode: bytes, offset: int) -> tuple[int, int]:
    """Read a register byte at *offset*; return ``(reg_value, new_offset)``."""
    return bytecode[offset], offset + 1


def read_i16(bytecode: bytes, offset: int) -> tuple[int, int]:
    """Read a signed 16-bit little-endian integer at *offset*.

    Returns ``(value, new_offset)``.
    """
    lo = bytecode[offset]
    hi = bytecode[offset + 1]
    val = lo | (hi << 8)
    if val >= 0x8000:
        val -= 0x10000
    return val, offset + 2


def read_u16(bytecode: bytes, offset: int) -> tuple[int, int]:
    """Read an unsigned 16-bit little-endian integer at *offset*."""
    lo = bytecode[offset]
    hi = bytecode[offset + 1]
    return (lo | (hi << 8)), offset + 2
