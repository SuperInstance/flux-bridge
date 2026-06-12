"""flux_bridge.bytecode — Python port of flux-core's bytecode module."""

from flux_bridge.bytecode.opcodes import (
    Op,
    from_byte,
    instruction_size,
    is_control_flow,
    is_alu,
    is_a2a,
    read_reg,
    read_i16,
    read_u16,
)

from flux_bridge.bytecode.assembler import Assembler

from flux_bridge.bytecode.disassembler import Disassembler, DisassembledInstruction

from flux_bridge.bytecode.validator import validate, ValidationResult

__all__ = [
    "Op",
    "from_byte",
    "instruction_size",
    "is_control_flow",
    "is_alu",
    "is_a2a",
    "read_reg",
    "read_i16",
    "read_u16",
    "Assembler",
    "Disassembler",
    "DisassembledInstruction",
    "validate",
    "ValidationResult",
]
