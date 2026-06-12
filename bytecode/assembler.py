"""
flux_bridge.bytecode.assembler — programmatic bytecode assembler.

Usage::

    from flux_bridge.bytecode import Assembler

    asm = Assembler()
    asm.emit_mov_i(0, 42)
    asm.emit_add(1, 0)
    asm.emit_halt()
    bytecode = asm.assemble()          # b'...'
    hex_str = asm.assemble_to_hex()    # '2b002a00...'
"""

from __future__ import annotations

import struct
from typing import Sequence

from flux_bridge.bytecode.opcodes import Op, from_byte


MAX_GP_REG: int = 15  # GP registers 0-15, FP registers 16-31 reserved


def _check_reg(r: int, context: str = "register") -> None:
    """Validate that *r* is a GP register (0-15)."""
    if not isinstance(r, int):
        raise TypeError(f"{context} must be int, got {type(r).__name__}")
    if r < 0 or r > MAX_GP_REG:
        raise ValueError(
            f"{context} out of range: {r} (valid 0-{MAX_GP_REG})"
        )


def _i16(v: int) -> bytes:
    """Pack *v* as a signed 16-bit little-endian value."""
    return struct.pack("<h", v)


# ── Assembler ──────────────────────────────────────────────────────── #

class Assembler:
    """Build flux-core bytecode instruction-by-instruction.

    Call *emit_* methods to append instructions, then call ``assemble()``
    to obtain the final ``bytes`` object.  Branch targets use string
    labels recorded via ``label()`` and patched via ``resolve_labels()``.
    Labels are resolved automatically during ``assemble()``.
    """

    def __init__(self) -> None:
        self._buf = bytearray()
        self._labels: dict[str, int] = {}
        self._fixups: list[tuple[int, int, str]] = []
        # ^ (patch_pos, instr_end, label_name)

    # ── label / fixup support ──────────────────────────────────────── #

    def label(self, name: str) -> None:
        """Mark the current position with *name* (e.g. ``"loop"``)."""
        self._labels[name] = len(self._buf)

    def resolve_labels(self) -> None:
        """Patch all pending label fixups.

        Called automatically by ``assemble()``, but also safe to call
        manually before ``assemble()`` if you need to inspect the buffer.
        """
        for patch_pos, instr_end, label in self._fixups:
            target = self._labels.get(label)
            if target is None:
                raise ValueError(f"Undefined label: {label!r}")
            offset = target - instr_end
            offset_i16 = offset & 0xFFFF  # little-endian i16
            self._buf[patch_pos] = offset_i16 & 0xFF
            self._buf[patch_pos + 1] = (offset_i16 >> 8) & 0xFF

    # ── emit helpers ───────────────────────────────────────────────── #

    def emit(self, op: Op) -> None:
        """Append a single opcode byte."""
        self._buf.append(op)

    def emit_op_reg(self, op: Op, r: int) -> None:
        """Append opcode + register byte."""
        _check_reg(r)
        self._buf.append(op)
        self._buf.append(r)

    def emit_op_reg_reg(self, op: Op, dst: int, src: int) -> None:
        """Append opcode + register + register (3 bytes)."""
        _check_reg(dst, "dst")
        _check_reg(src, "src")
        self._buf.append(op)
        self._buf.append(dst)
        self._buf.append(src)

    def emit_op_reg_i16(self, op: Op, r: int, imm: int) -> None:
        """Append opcode + register + i16 immediate (4 bytes)."""
        _check_reg(r)
        self._buf.append(op)
        self._buf.append(r)
        self._buf.extend(_i16(imm))

    def emit_op_pad_i16(self, op: Op, imm: int) -> None:
        """Append opcode + padding byte + i16 immediate (4 bytes)."""
        self._buf.append(op)
        self._buf.append(0)
        self._buf.extend(_i16(imm))

    def _emit_branch_fixup(
        self, op: Op, r: int, label: str
    ) -> None:
        """Append a branch op with a label that will be patched later.

        Format: opcode(1) + reg(1) + i16-placeholder(2) = 4 bytes.
        The placeholder is recorded so ``resolve_labels()`` can patch it.
        """
        pos = len(self._buf)
        _check_reg(r)
        self._buf.append(op)
        self._buf.append(r)
        self._buf.extend(b"\x00\x00")
        self._fixups.append((pos + 2, pos + 4, label))

    def _emit_jump_fixup(self, op: Op, label: str) -> None:
        """Append a jump/call with a label (JMP/CALL — pad byte + i16)."""
        pos = len(self._buf)
        self._buf.append(op)
        self._buf.append(0)  # padding byte
        self._buf.extend(b"\x00\x00")
        self._fixups.append((pos + 2, pos + 4, label))

    # ── individual instruction emitters ────────────────────────────── #

    def emit_nop(self) -> None:
        self.emit(Op.NOP)

    def emit_halt(self) -> None:
        self.emit(Op.HALT)

    def emit_yield(self) -> None:
        self.emit(Op.YIELD)

    def emit_dup(self) -> None:
        self.emit(Op.DUP)

    def emit_inc(self, r: int) -> None:
        self.emit_op_reg(Op.INC, r)

    def emit_dec(self, r: int) -> None:
        self.emit_op_reg(Op.DEC, r)

    def emit_ineg(self, r: int) -> None:
        self.emit_op_reg(Op.INEG, r)

    def emit_inot(self, r: int) -> None:
        self.emit_op_reg(Op.INOT, r)

    def emit_push(self, r: int) -> None:
        self.emit_op_reg(Op.PUSH, r)

    def emit_pop(self, r: int) -> None:
        self.emit_op_reg(Op.POP, r)

    def emit_mov(self, dst: int, src: int) -> None:
        """MOV dst, src  — copy register to register (3 bytes)."""
        self.emit_op_reg_reg(Op.MOV, dst, src)

    def emit_mov_i(self, dst: int, imm: int) -> None:
        """MOVI dst, imm — load 16-bit signed immediate (4 bytes)."""
        self.emit_op_reg_i16(Op.MOVI, dst, imm)

    def emit_add(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IADD, a, b)

    def emit_sub(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.ISUB, a, b)

    def emit_mul(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IMUL, a, b)

    def emit_div(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IDIV, a, b)

    def emit_mod(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IMOD, a, b)

    def emit_and(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IAND, a, b)

    def emit_or(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IOR, a, b)

    def emit_xor(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.IXOR, a, b)

    def emit_shl(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.ISHL, a, b)

    def emit_shr(self, a: int, b: int) -> None:
        self.emit_op_reg_reg(Op.ISHR, a, b)

    def emit_cmp(self, a: int, b: int) -> None:
        """CMP a, b — set flags based on a - b."""
        self.emit_op_reg_reg(Op.CMP, a, b)

    def emit_ret(self) -> None:
        """RET — return from call (3 bytes: op + pad + pad)."""
        self._buf.append(Op.RET)
        self._buf.append(0)
        self._buf.append(0)

    def emit_jmp(self, offset: int | str) -> None:
        """JMP offset  — unconditional branch.

        *offset* can be:
        - An ``int`` absolute byte offset (resolved immediately).
        - A ``str`` label name (resolved during ``resolve_labels()``).
        """
        if isinstance(offset, int):
            self._emit_jump_fixup_imm(Op.JMP, offset)
        else:
            self._emit_jump_fixup(Op.JMP, offset)

    def emit_jz(self, reg: int, offset: int | str) -> None:
        """JZ reg, offset — jump if register == 0."""
        if isinstance(offset, int):
            self._emit_branch_fixup_imm(Op.JZ, reg, offset)
        else:
            self._emit_branch_fixup(Op.JZ, reg, offset)

    def emit_jnz(self, reg: int, offset: int | str) -> None:
        """JNZ reg, offset — jump if register != 0."""
        if isinstance(offset, int):
            self._emit_branch_fixup_imm(Op.JNZ, reg, offset)
        else:
            self._emit_branch_fixup(Op.JNZ, reg, offset)

    def emit_call(self, offset: int | str) -> None:
        """CALL offset — push return address and jump."""
        if isinstance(offset, int):
            self._emit_jump_fixup_imm(Op.CALL, offset)
        else:
            self._emit_jump_fixup(Op.CALL, offset)

    def _emit_jump_fixup_imm(self, op: Op, offset: int) -> None:
        """Append JMP/CALL with immediate resolved offset."""
        self._buf.append(op)
        self._buf.append(0)  # padding
        self._buf.extend(_i16(offset))

    def _emit_branch_fixup_imm(self, op: Op, reg: int, offset: int) -> None:
        """Append JZ/JNZ with immediate resolved offset."""
        _check_reg(reg)
        self._buf.append(op)
        self._buf.append(reg)
        self._buf.extend(_i16(offset))

    # ── A2A (Agent-to-Agent) ──────────────────────────────────────── #

    def emit_tell(self, payload: bytes) -> None:
        """TELL payload — send a message to a target agent.

        Encoding: opcode(1) + u16 length(2) + payload(N).
        """
        self._emit_a2a(Op.TELL, payload)

    def emit_ask(self, payload: bytes) -> None:
        """ASK payload — send a query to a target agent.

        Encoding: opcode(1) + u16 length(2) + payload(N).
        """
        self._emit_a2a(Op.ASK, payload)

    def emit_delegate(self, payload: bytes) -> None:
        """DELEGATE payload — delegate a task to another agent.

        Encoding: opcode(1) + u16 length(2) + payload(N).
        """
        self._emit_a2a(Op.DELEGATE, payload)

    def emit_broadcast(self, payload: bytes) -> None:
        """BROADCAST payload — broadcast a message to all agents.

        Encoding: opcode(1) + u16 length(2) + payload(N).
        """
        self._emit_a2a(Op.BROADCAST, payload)

    def _emit_a2a(self, op: Op, payload: bytes) -> None:
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError(f"payload must be bytes, got {type(payload).__name__}")
        self._buf.append(op)
        # u16 little-endian length
        length = len(payload)
        if length > 0xFFFF:
            raise ValueError(f"payload too long: {length} (max 65535)")
        self._buf.append(length & 0xFF)
        self._buf.append((length >> 8) & 0xFF)
        self._buf.extend(payload)

    # ── final assembly ─────────────────────────────────────────────── #

    def assemble(self) -> bytes:
        """Resolve labels and return the final bytecode as ``bytes``."""
        self.resolve_labels()
        return bytes(self._buf)

    def assemble_to_hex(self) -> str:
        """Resolve labels and return the bytecode as a hex string."""
        self.resolve_labels()
        return self._buf.hex()

    @property
    def buffer(self) -> bytearray:
        """Access the raw buffer (before label resolution)."""
        return self._buf

    def size(self) -> int:
        """Return current buffer length in bytes."""
        return len(self._buf)
