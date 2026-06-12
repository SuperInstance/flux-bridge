"""
flux_bridge.bytecode.disassembler — reverse bytes back into instructions.

Matches the behaviour of flux-core's ``Disassembler`` in
``src/bytecode/disassembler.rs``.
"""

from __future__ import annotations

from flux_bridge.bytecode.opcodes import (
    Op,
    from_byte,
    read_reg,
    read_i16,
    read_u16,
    instruction_size,
    is_a2a,
)


# ── data types ─────────────────────────────────────────────────────── #

class DisassembledInstruction:
    """A single decoded instruction.

    Attributes
    ----------
    offset : int
        Byte offset in the original bytecode.
    opcode : Op
        The decoded operation.
    op_name : str
        Human-readable mnemonic (e.g. ``"MOVI"``, ``"JZ"``).
    args : list[str]
        Argument strings (e.g. ``["R0"]``, ``["R0", "42"]``).
    size : int
        Total size of this instruction in bytes.
    text : str
        Full rendered text (e.g. ``"MOVI R0, 42"``).
    """

    __slots__ = ("offset", "opcode", "op_name", "args", "size", "text")

    def __init__(
        self,
        offset: int,
        opcode: Op,
        op_name: str,
        args: list[str],
        size: int,
        text: str,
    ) -> None:
        self.offset = offset
        self.opcode = opcode
        self.op_name = op_name
        self.args = args
        self.size = size
        self.text = text

    def __repr__(self) -> str:
        return (
            f"DisassembledInstruction(offset={self.offset}, "
            f"size={self.size}, text={self.text!r})"
        )

    def __str__(self) -> str:
        return self.text


# ── Disassembler ───────────────────────────────────────────────────── #

class Disassembler:
    """Decode flux-core bytecode into a list of instructions."""

    @staticmethod
    def disassemble(bytecode: bytes) -> list[DisassembledInstruction]:
        """Return an ordered list of ``DisassembledInstruction`` entries.

        Truncated instructions are reported with a ``(truncated)`` suffix
        rather than raising an error, matching the Rust behaviour.
        """
        instructions: list[DisassembledInstruction] = []
        pc = 0
        length = len(bytecode)

        while pc < length:
            offset = pc
            op_byte = bytecode[pc]
            op = from_byte(op_byte)
            pc += 1

            if op is None:
                # Unknown opcode
                inst = DisassembledInstruction(
                    offset=offset,
                    opcode=Op.NOP,  # fallback
                    op_name="???",
                    args=[f"0x{op_byte:02X}"],
                    size=1,
                    text=f"??? (0x{op_byte:02X})",
                )
                instructions.append(inst)
                continue

            op_name = op.name

            if is_a2a(op):
                # Variable-length: opcode + u16 length + payload
                if pc + 1 >= length:
                    # truncated length field
                    inst = DisassembledInstruction(
                        offset=offset, opcode=op, op_name=op_name,
                        args=[], size=pc - offset,
                        text=f"{op_name} (truncated)",
                    )
                    instructions.append(inst)
                    continue

                payload_len = bytecode[pc] | (bytecode[pc + 1] << 8)
                pc += 2

                available = length - pc
                actual_len = min(payload_len, available)
                payload = bytecode[pc:pc + actual_len]
                pc += actual_len

                truncated = actual_len < payload_len
                hex_repr = payload.hex()
                if truncated:
                    text = f"{op_name} <{actual_len}/{payload_len} bytes> {hex_repr} (truncated)"
                else:
                    text = f"{op_name} <{payload_len} bytes> {hex_repr}"

                args = [hex_repr] if hex_repr else []
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=args, size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # ── fixed-size instructions ── #
            try:
                expected = instruction_size(op)
            except ValueError:
                # shouldn't happen for non-A2A, but safety net
                expected = 1

            if expected == 1:
                # NOP, DUP, HALT, YIELD
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[], size=pc - offset, text=op_name,
                )
                instructions.append(inst)
                continue

            # Check we have enough bytes remaining
            remaining = length - pc
            needed = expected - 1  # we already consumed the opcode byte
            if remaining < needed:
                text = f"{op_name} (truncated)"
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # ── 2-byte instructions: op + reg ── #
            if op in (Op.INC, Op.DEC, Op.INEG, Op.INOT, Op.PUSH, Op.POP):
                r, pc = read_reg(bytecode, pc)
                text = f"{op_name} R{r}"
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[f"R{r}"], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # ── 3-byte instructions: op + reg + reg ── #
            if op in (
                Op.MOV, Op.IADD, Op.ISUB, Op.IMUL, Op.IDIV, Op.IMOD,
                Op.IAND, Op.IOR, Op.IXOR, Op.ISHL, Op.ISHR,
                Op.CMP,
                Op.FADD, Op.FSUB, Op.FMUL, Op.FDIV,
                Op.LOAD, Op.STORE,
            ):
                d, pc = read_reg(bytecode, pc)
                s, pc = read_reg(bytecode, pc)
                text = f"{op_name} R{d}, R{s}"
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[f"R{d}", f"R{s}"], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # RET — 3 bytes (op + pad + pad)
            if op == Op.RET:
                # skip padding bytes
                pc += 2
                text = op_name
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # ── 4-byte instructions ── #
            # MOVI: op + reg + i16
            if op == Op.MOVI:
                r, pc = read_reg(bytecode, pc)
                imm, pc = read_i16(bytecode, pc)
                text = f"{op_name} R{r}, {imm}"
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[f"R{r}", str(imm)], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # JMP, CALL: op + pad + i16
            if op in (Op.JMP, Op.CALL):
                # skip padding byte
                pc += 1
                off, pc = read_i16(bytecode, pc)
                text = f"{op_name} {off}"
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[str(off)], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # JZ, JNZ: op + reg + i16
            if op in (Op.JZ, Op.JNZ):
                r, pc = read_reg(bytecode, pc)
                off, pc = read_i16(bytecode, pc)
                text = f"{op_name} R{r}, {off}"
                inst = DisassembledInstruction(
                    offset=offset, opcode=op, op_name=op_name,
                    args=[f"R{r}", str(off)], size=pc - offset, text=text,
                )
                instructions.append(inst)
                continue

            # Fallback (shouldn't be reached)
            text = f"{op_name} (unknown encoding)"
            inst = DisassembledInstruction(
                offset=offset, opcode=op, op_name=op_name,
                args=[], size=pc - offset, text=text,
            )
            instructions.append(inst)

        return instructions

    @staticmethod
    def disassemble_to_text(bytecode: bytes) -> str:
        """Return a human-readable disassembly listing.

        One instruction per line with hex offset prefix, e.g.::

            0x00  MOVI R0, 42
            0x04  IADD R1, R0
            0x07  HALT
        """
        instrs = Disassembler.disassemble(bytecode)
        lines = []
        for inst in instrs:
            lines.append(f"  0x{inst.offset:04X}  {inst.text}")
        return "\n".join(lines)
