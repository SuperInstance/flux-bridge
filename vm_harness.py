"""
flux_bridge.vm_harness — VM compilation, execution, and pure-Python fallback.

Provides:

- ``VMHarness`` — manages flux-core Rust compilation + native subprocess execution.
- ``PythonInterpreter`` — pure Python VM interpreter that exactly mirrors the
  Rust ``Interpreter`` in *flux-core/src/vm/interpreter.rs*.
- ``VMResult`` — result dataclass returned by both execution paths.
- Snapshot/restore for state serialisation (training rollback).
- Benchmarks comparing Python vs native throughput.

Usage::

    from flux_bridge.vm_harness import PythonInterpreter, VMResult

    # Pure-Python mode (no Rust needed)
    vm = PythonInterpreter(debug=True)
    bc = bytes([0x2B, 0, 42, 0, 0x80])  # MOVI R0, 42 ; HALT
    result = vm.execute(bc)
    assert result.registers[0] == 42

    # Native mode (requires ``cargo build``)
    from flux_bridge.vm_harness import VMHarness
    result = VMHarness().execute(bc)
    assert result.registers[0] == 42
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FLUX_CORE_DIR = Path(__file__).resolve().parent.parent / "flux-core"
_VM_CLI_DIR = Path(__file__).resolve().parent / "vm-cli"
_BINARY_CACHE: dict[str, Optional[str]] = {"path": None}


# ---------------------------------------------------------------------------
# Exceptions — mirrors flux-core/src/error.rs
# ---------------------------------------------------------------------------

class FluxError(Exception):
    """Base for all VM errors, paralleling ``flux_core::error::FluxError``."""

class DivisionByZero(FluxError):
    def __str__(self) -> str:
        return "Division by zero"

class InvalidOpcode(FluxError):
    def __init__(self, opcode: int) -> None:
        self.opcode = opcode
    def __str__(self) -> str:
        return f"Invalid opcode: 0x{self.opcode:02X}"

class InvalidRegister(FluxError):
    def __init__(self, reg: int) -> None:
        self.reg = reg
    def __str__(self) -> str:
        return f"Invalid register: R{self.reg}"

class StackOverflow(FluxError):
    def __str__(self) -> str:
        return "Stack overflow"

class StackUnderflow(FluxError):
    def __str__(self) -> str:
        return "Stack underflow"

class CycleBudgetExceeded(FluxError):
    def __init__(self, budget: int) -> None:
        self.budget = budget
    def __str__(self) -> str:
        return f"Cycle budget exceeded: {self.budget}"

class InvalidBytecode(FluxError):
    def __init__(self, msg: str = "") -> None:
        self.msg = msg
    def __str__(self) -> str:
        return f"Invalid bytecode: {self.msg}"


# ---------------------------------------------------------------------------
# VMResult — mirrors the JSON output from flux-vm CLI
# ---------------------------------------------------------------------------

@dataclass
class VMResult:
    """Result of executing a bytecode program.

    Attributes match the Rust interpreter's observable state after execution.
    """
    success: bool = True
    registers: list[int] = field(default_factory=lambda: [0] * 16)
    fp_registers: list[float] = field(default_factory=lambda: [0.0] * 16)
    cycles_used: int = 0
    halted: bool = False
    pc: int = 0
    flag_zero: bool = False
    flag_sign: bool = False
    error: Optional[str] = None
    stack: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MASK32 = 0xFFFFFFFF
_SIGN_BIT = 0x80000000


def _i32(val: int) -> int:
    """Wrap a Python integer to signed 32-bit two's complement (like Rust i32)."""
    val &= _MASK32
    if val >= _SIGN_BIT:
        val -= 0x1_0000_0000
    return val


def _read_u8(bc: bytes, pos: int) -> tuple[int, int]:
    """Read one unsigned byte from *bc* at *pos*; return ``(value, new_pos)``."""
    return bc[pos], pos + 1


def _read_i16(bc: bytes, pos: int) -> tuple[int, int]:
    """Read a signed 16-bit little-endian integer from *bc* at *pos*."""
    lo = bc[pos]
    hi = bc[pos + 1]
    val = lo | (hi << 8)
    if val >= 0x8000:
        val -= 0x10000
    return val, pos + 2


# ---------------------------------------------------------------------------
# Pure Python Interpreter — exactly mirrors Rust's interpreter.rs
# ---------------------------------------------------------------------------

class PythonInterpreter:
    """Pure-Python implementation of the flux-core VM interpreter.

    This is a byte-for-byte faithful port of
    *flux-core/src/vm/interpreter.rs* and *registers.rs*.

    Supported opcodes (matching the Rust interpreter's ``execute()`` match):
    0x00 NOP · 0x01 MOV · 0x04 JMP · 0x05 JZ · 0x06 JNZ · 0x07 CALL ·
    0x08 IADD · 0x09 ISUB · 0x0A IMUL · 0x0B IDIV · 0x0C IMOD ·
    0x0D INEG · 0x0E INC · 0x0F DEC ·
    0x10 IAND · 0x11 IOR · 0x12 IXOR · 0x13 INOT ·
    0x20 PUSH · 0x21 POP · 0x22 DUP ·
    0x28 RET · 0x2B MOVI · 0x2D CMP ·
    0x80 HALT · 0x81 YIELD
    """

    def __init__(self, debug: bool = False, max_cycles: int = 10_000_000) -> None:
        self.debug = debug

        # Register file — matches registers.rs layout
        self.gp: list[int] = [0] * 16       # 16 general-purpose i32
        self.fp: list[float] = [0.0] * 16   # 16 float registers
        self.pc: int = 0                     # program counter
        self.sp: int = 0                     # stack pointer (reserved)
        self.flag_zero: bool = False
        self.flag_sign: bool = False

        # Execution state
        self.halted: bool = False
        self._last_error_str: Optional[str] = None
        self.cycle_count: int = 0
        self.max_cycles: int = max_cycles
        self.stack: list[int] = []

    # -- flags ---------------------------------------------------------

    def _set_flags(self, result: int) -> None:
        """Equivalent to ``RegisterFile::set_flags``."""
        self.flag_zero = result == 0
        self.flag_sign = result < 0

    # -- execute -------------------------------------------------------

    def execute(self, bytecode: bytes) -> VMResult:
        """Run the bytecode program and return a ``VMResult``.

        This is a direct port of ``Interpreter::execute()``.
        Always resets state first (like Rust's ``Interpreter::new``).
        """
        self.reset()
        bc_len = len(bytecode)

        try:
            while not self.halted and self.cycle_count < self.max_cycles:
                if self.pc >= bc_len:
                    break

                op_byte = bytecode[self.pc]
                self.pc += 1
                self.cycle_count += 1

                if self.debug:
                    self._debug_trace(op_byte, bytecode)

                # ── Opcode dispatch ──────────────────────────────────
                if op_byte == 0x00:        # NOP
                    pass

                elif op_byte == 0x01:      # MOV  dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        self.gp[d] = self.gp[s]

                elif op_byte == 0x04:      # JMP  _r, offset
                    _, self.pc = _read_u8(bytecode, self.pc)
                    off, self.pc = _read_i16(bytecode, self.pc)
                    self.pc = _i32(self.pc + off)

                elif op_byte == 0x05:      # JZ   r, offset
                    r, self.pc = _read_u8(bytecode, self.pc)
                    off, self.pc = _read_i16(bytecode, self.pc)
                    if r < 16 and self.gp[r] == 0:
                        self.pc = _i32(self.pc + off)

                elif op_byte == 0x06:      # JNZ  r, offset
                    r, self.pc = _read_u8(bytecode, self.pc)
                    off, self.pc = _read_i16(bytecode, self.pc)
                    if r < 16 and self.gp[r] != 0:
                        self.pc = _i32(self.pc + off)

                elif op_byte == 0x07:      # CALL _r, offset
                    _, self.pc = _read_u8(bytecode, self.pc)
                    off, self.pc = _read_i16(bytecode, self.pc)
                    self.stack.append(self.pc)
                    self.pc = _i32(self.pc + off)

                elif op_byte == 0x08:      # IADD dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        r = _i32(self.gp[d] + self.gp[s])
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x09:      # ISUB dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        r = _i32(self.gp[d] - self.gp[s])
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x0A:      # IMUL dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        r = _i32(self.gp[d] * self.gp[s])
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x0B:      # IDIV dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if s < 16 and self.gp[s] == 0:
                        raise DivisionByZero()
                    if d < 16 and s < 16:
                        r = _i32(self.gp[d] // self.gp[s])
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x0C:      # IMOD dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if s < 16 and self.gp[s] == 0:
                        raise DivisionByZero()
                    if d < 16 and s < 16:
                        r = _i32(self.gp[d] % self.gp[s])
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x0D:      # INEG dst
                    d, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16:
                        r = _i32(-self.gp[d])
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x0E:      # INC  dst
                    d, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16:
                        r = _i32(self.gp[d] + 1)
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x0F:      # DEC  dst
                    d, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16:
                        r = _i32(self.gp[d] - 1)
                        self.gp[d] = r
                        self._set_flags(r)

                elif op_byte == 0x10:      # IAND dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        self.gp[d] = self.gp[d] & self.gp[s]

                elif op_byte == 0x11:      # IOR  dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        self.gp[d] = self.gp[d] | self.gp[s]

                elif op_byte == 0x12:      # IXOR dst, src
                    d, self.pc = _read_u8(bytecode, self.pc)
                    s, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16 and s < 16:
                        self.gp[d] = self.gp[d] ^ self.gp[s]

                elif op_byte == 0x13:      # INOT dst
                    d, self.pc = _read_u8(bytecode, self.pc)
                    if d < 16:
                        self.gp[d] = _i32(~self.gp[d])

                elif op_byte == 0x20:      # PUSH r
                    r, self.pc = _read_u8(bytecode, self.pc)
                    if r < 16:
                        self.stack.append(self.gp[r])

                elif op_byte == 0x21:      # POP  r
                    r, self.pc = _read_u8(bytecode, self.pc)
                    if r < 16 and self.stack:
                        self.gp[r] = self.stack.pop()

                elif op_byte == 0x22:      # DUP
                    if self.stack:
                        self.stack.append(self.stack[-1])

                elif op_byte == 0x28:      # RET  _r, _p
                    _, self.pc = _read_u8(bytecode, self.pc)
                    _, self.pc = _read_u8(bytecode, self.pc)
                    if self.stack:
                        self.pc = self.stack.pop()

                elif op_byte == 0x2B:      # MOVI d, imm16
                    d, self.pc = _read_u8(bytecode, self.pc)
                    imm, self.pc = _read_i16(bytecode, self.pc)
                    if d < 16:
                        self.gp[d] = imm

                elif op_byte == 0x2D:      # CMP  a, b
                    a, self.pc = _read_u8(bytecode, self.pc)
                    b, self.pc = _read_u8(bytecode, self.pc)
                    if a < 16 and b < 16:
                        self.flag_zero = self.gp[a] == self.gp[b]
                        self.flag_sign = self.gp[a] < self.gp[b]

                elif op_byte == 0x80:      # HALT
                    self.halted = True

                elif op_byte == 0x81:      # YIELD — NOP equivalent
                    pass

                else:
                    raise InvalidOpcode(op_byte)

        except FluxError as exc:
            self._last_error_str = str(exc)
            return self._result(success=False, error=str(exc))

        # After execution — check cycle budget (matches Rust logic)
        if self.cycle_count >= self.max_cycles and not self.halted:
            return self._result(
                success=False,
                error=str(CycleBudgetExceeded(self.max_cycles)),
            )

        return self._result(success=True)

    def _result(self, success: bool = True, error: Optional[str] = None) -> VMResult:
        """Build a ``VMResult`` from current interpreter state."""
        return VMResult(
            success=success,
            registers=list(self.gp),
            fp_registers=list(self.fp),
            cycles_used=self.cycle_count,
            halted=self.halted,
            pc=self.pc,
            flag_zero=self.flag_zero,
            flag_sign=self.flag_sign,
            error=error or (None if success else self._last_error()),
            stack=list(self.stack),
        )

    def _last_error(self) -> Optional[str]:
        """Return the most recent FluxError string, if any was raised."""
        return self._last_error_str

    def _debug_trace(self, op_byte: int, bc: bytes) -> None:
        """Print a one-line trace of the current instruction (debug mode)."""
        from flux_bridge.bytecode.opcodes import from_byte
        op = from_byte(op_byte)
        op_name = op.name if op else f"0x{op_byte:02X}"
        regs_preview = self.gp[:4]
        stack_top = self.stack[-3:] if self.stack else []
        print(
            f"[{self.cycle_count:>6}] "
            f"PC={self.pc - 1:#06x} "
            f"{op_name:>6} "
            f"R0={regs_preview[0]} R1={regs_preview[1]} "
            f"R2={regs_preview[2]} R3={regs_preview[3]} "
            f"stack={stack_top} "
            f"Z={'1' if self.flag_zero else '0'} "
            f"S={'1' if self.flag_sign else '0'}",
            file=sys.stderr,
        )

    # -- convenience read access (matching Interpreter) ----------------

    def read_gp(self, idx: int) -> int:
        """Read a general-purpose register (bounds-checked)."""
        if 0 <= idx < 16:
            return self.gp[idx]
        return 0

    def read_fp(self, idx: int) -> float:
        """Read a float register (bounds-checked)."""
        if 0 <= idx < 16:
            return self.fp[idx]
        return 0.0

    def write_gp(self, idx: int, val: int) -> None:
        """Write a general-purpose register (bounds-checked)."""
        if 0 <= idx < 16:
            self.gp[idx] = val

    def write_fp(self, idx: int, val: float) -> None:
        """Write a float register (bounds-checked)."""
        if 0 <= idx < 16:
            self.fp[idx] = val

    # -- reset ---------------------------------------------------------

    def reset(self) -> None:
        """Reset interpreter state to post-construction defaults."""
        self.gp = [0] * 16
        self.fp = [0.0] * 16
        self.pc = 0
        self.sp = 0
        self.flag_zero = False
        self.flag_sign = False
        self.halted = False
        self.cycle_count = 0
        self._last_error_str = None
        self.stack.clear()


# ---------------------------------------------------------------------------
# Snapshot / Restore  (rollback for training / replay)
# ---------------------------------------------------------------------------

_SNAPSHOT_FORMAT = struct.Struct("<16i 16d I ?B ?B I I")


def snapshot(
    gp: list[int],
    fp: list[float],
    stack: list[int],
    pc: int,
    *,
    flag_zero: bool = False,
    flag_sign: bool = False,
    cycle_count: int = 0,
) -> bytes:
    """Serialise VM state into a compact binary snapshot (89 + 4×len(stack) bytes).

    Suitable for rollback during RL training or deterministic replay.
    """
    stack_len = len(stack)
    header = _SNAPSHOT_FORMAT.pack(
        *gp,              # 16 i32
        *fp,              # 16 f64 (128 bytes — big, but matches register file)
        pc,               # u32
        1 if flag_zero else 0,
        1 if flag_sign else 0,
        cycle_count & 0xFFFF_FFFF,       # low 32 bits of cycle count
        (cycle_count >> 32) & 0xFFFF_FFFF,  # high 32 bits
    )
    tail = struct.pack(f"<{stack_len}i", *stack)
    return header + tail


def restore(data: bytes) -> dict[str, Any]:
    """Deserialise a binary snapshot created by ``snapshot()``.

    Returns a dict with keys: ``gp``, ``fp``, ``pc``, ``flag_zero``,
    ``flag_sign``, ``cycle_count``, ``stack``.
    """
    hdr_size = _SNAPSHOT_FORMAT.size
    header = data[:hdr_size]
    tail = data[hdr_size:]

    values = _SNAPSHOT_FORMAT.unpack(header)
    gp = list(values[0:16])
    fp = list(values[16:32])
    pc = values[32]
    flag_zero = bool(values[33])
    flag_sign = bool(values[34])
    cycles_low = values[35]
    cycles_high = values[36]
    cycle_count = cycles_low | (cycles_high << 32)

    stack = list(struct.unpack(f"<{len(tail) // 4}i", tail)) if tail else []

    return {
        "gp": gp,
        "fp": fp,
        "pc": pc,
        "flag_zero": flag_zero,
        "flag_sign": flag_sign,
        "cycle_count": cycle_count,
        "stack": stack,
    }


# ---------------------------------------------------------------------------
# VMHarness — manages Rust compilation + native execution
# ---------------------------------------------------------------------------

class VMHarness:
    """Compiles flux-core and executes bytecode via the native ``flux-vm`` binary.

    If the Rust toolchain is unavailable, falls back to a ``PythonInterpreter``
    instance transparently.
    """

    def __init__(
        self,
        release: bool = True,
        max_cycles: int = 10_000_000,
        binary_path: Optional[str] = None,
    ) -> None:
        """
        Args:
            release: Compile in release mode (default) or debug.
            max_cycles: Default max instruction cycles per execute() call.
            binary_path: Explicit path to pre-built ``flux-vm`` binary.
                         If given, ``build()`` is skipped.
        """
        self.release = release
        self.max_cycles = max_cycles
        self._binary_path: Optional[str] = binary_path
        self._fallback: Optional[PythonInterpreter] = None

    # -- build ---------------------------------------------------------

    @staticmethod
    def build(release: bool = False) -> Optional[str]:
        """Compile flux-core + the CLI wrapper.

        Returns the path to the ``flux-vm`` binary, or ``None`` if the
        Rust toolchain is not available (caller should fall back to the
        Python interpreter).

        The binary is cached after first successful build.
        """
        if _BINARY_CACHE["path"] is not None:
            return _BINARY_CACHE["path"]

        # 1. Check for rustc / cargo
        if not _rust_available():
            return None

        # 2. Build the CLI wrapper crate first (it depends on flux-core,
        #    so this compiles both)
        profile = "release" if release else "debug"
        result = subprocess.run(
            ["cargo", "build", "--" if not release else "--release"],
            cwd=str(_VM_CLI_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode != 0:
            # Try debug as fallback
            if release:
                result = subprocess.run(
                    ["cargo", "build"],
                    cwd=str(_VM_CLI_DIR),
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
            if result.returncode != 0:
                print(
                    f"[VMHarness] cargo build failed:\n{result.stderr}",
                    file=sys.stderr,
                )
                return None

        binary_name = "flux-vm"
        binary_path = str(_VM_CLI_DIR / "target" / profile / binary_name)

        # Also check the flux-core target dir as fallback
        if not os.path.isfile(binary_path):
            alt = str(_FLUX_CORE_DIR / "target" / profile / binary_name)
            if os.path.isfile(alt):
                binary_path = alt

        if os.path.isfile(binary_path):
            _BINARY_CACHE["path"] = binary_path
            return binary_path

        return None

    # -- execute -------------------------------------------------------

    def execute(
        self,
        bytecode: bytes,
        registers: Optional[list[int]] = None,
        max_cycles: Optional[int] = None,
    ) -> VMResult:
        """Execute bytecode via the native ``flux-vm`` binary, or fall back
        to the pure-Python interpreter.

        Args:
            bytecode: Raw bytecode bytes.
            registers: Optional initial GP register values (list of 16 ints).
                       Passed via environment for the native binary.
            max_cycles: Override the default max cycles for this call.
        """
        cycles = max_cycles if max_cycles is not None else self.max_cycles

        # Determine execution path
        binary = self._binary_path or _BINARY_CACHE["path"]
        if binary is None:
            binary = self.build(release=self.release)

        if binary is not None and os.path.isfile(binary):
            return self._execute_native(bytecode, registers, cycles)
        else:
            return self._execute_python(bytecode, registers, cycles)

    def _execute_native(
        self,
        bytecode: bytes,
        registers: Optional[list[int]],
        max_cycles: int,
    ) -> VMResult:
        """Execute via the Rust ``flux-vm`` binary."""
        env = os.environ.copy()
        env["MAX_CYCLES"] = str(max_cycles)

        # Preload register state by writing to a temp file the binary reads.
        # The current CLI doesn't support initial registers from stdin;
        # encode them in an env var for simplicity.
        if registers is not None and len(registers) == 16:
            env["FLUX_INIT_REGS"] = json.dumps(registers)

        try:
            proc = subprocess.run(
                [_BINARY_CACHE["path"]],
                input=bytecode,
                capture_output=True,
                timeout=60,
                env=env,
            )
        except FileNotFoundError:
            return self._execute_python(bytecode, registers, max_cycles)
        except subprocess.TimeoutExpired:
            return VMResult(
                success=False,
                error="Native binary timed out after 60s",
                cycles_used=0,
            )

        stdout = proc.stdout
        stderr = proc.stderr

        # Parse JSON output
        raw = stdout.decode("utf-8", errors="replace").strip()
        if not raw:
            return VMResult(
                success=False,
                error=f"Native binary produced no stdout. stderr: {stderr.decode('utf-8', errors='replace')[:500]}",
                stdout_bytes=stdout,
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return VMResult(
                success=False,
                error=f"JSON parse error from native binary: {e}. raw={raw[:500]}",
                stdout_bytes=stdout,
            )

        return VMResult(
            success=data.get("success", False),
            registers=data.get("registers", [0] * 16),
            fp_registers=data.get("fp_registers", [0.0] * 16),
            cycles_used=data.get("cycles_used", 0),
            halted=data.get("halted", False),
            pc=data.get("pc", 0),
            flag_zero=data.get("flag_zero", False),
            flag_sign=data.get("flag_sign", False),
            error=data.get("error") or None,
            stack=data.get("stack", []),
        )

    def _execute_python(
        self,
        bytecode: bytes,
        registers: Optional[list[int]],
        max_cycles: int,
    ) -> VMResult:
        """Execute via the pure Python fallback interpreter."""
        if self._fallback is None:
            self._fallback = PythonInterpreter(max_cycles=max_cycles)
        else:
            self._fallback.reset()
            self._fallback.max_cycles = max_cycles

        # Load initial register state
        if registers is not None:
            for i, v in enumerate(registers[:16]):
                self._fallback.gp[i] = v

        return self._fallback.execute(bytecode)


# ---------------------------------------------------------------------------
# Rust toolchain detection
# ---------------------------------------------------------------------------

def _rust_available() -> bool:
    """Return ``True`` if ``rustc`` and ``cargo`` are on ``$PATH``."""
    try:
        r1 = subprocess.run(
            ["rustc", "--version"], capture_output=True, timeout=5
        )
        r2 = subprocess.run(
            ["cargo", "--version"], capture_output=True, timeout=5
        )
        return r1.returncode == 0 and r2.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

def benchmark_vs_native(
    bytecode: bytes,
    iterations: int = 100,
    *,
    release: bool = True,
) -> dict[str, Any]:
    """Benchmark Python interpreter vs native binary throughput.

    Args:
        bytecode: Bytecode program to run each iteration.
        iterations: Number of repetitions.
        release: Whether to build the native binary in release mode.

    Returns:
        A dict with timing statistics::

            {
                "python": {"mean_s", …, "cycles_per_sec": …},
                "native": {"mean_s", …, "cycles_per_sec": …, "available": bool},
            }

    The ``native`` section will have ``available=False`` if the Rust binary
    could not be built or is missing.
    """
    # Warm up: build native binary + run a few iterations in Python
    harness = VMHarness(release=release)
    py = PythonInterpreter()
    _ = py.execute(bytecode)  # warm-up

    # ── Python timing ────────────────────────────────────────────────
    py_times: list[float] = []
    for _ in range(iterations):
        py.reset()
        t0 = time.perf_counter()
        r = py.execute(bytecode)
        t1 = time.perf_counter()
        py_times.append(t1 - t0)

    # ── Native timing (if available) ──────────────────────────────────
    native_times: list[float] = []
    native_ok = False
    binary = harness.build(release=release)
    if binary and os.path.isfile(binary):
        native_ok = True
        env = os.environ.copy()
        env["MAX_CYCLES"] = str(harness.max_cycles)

        for _ in range(iterations):
            t0 = time.perf_counter()
            subprocess.run(
                [binary],
                input=bytecode,
                capture_output=True,
                timeout=60,
                env=env,
            )
            t1 = time.perf_counter()
            native_times.append(t1 - t0)

    # ── Stats ────────────────────────────────────────────────────────
    def _stats(times: list[float]) -> dict[str, Any]:
        n = len(times)
        if n == 0:
            return {"available": False}
        mean_s = sum(times) / n
        variance = sum((t - mean_s) ** 2 for t in times) / n
        return {
            "available": True,
            "n": n,
            "mean_s": mean_s,
            "min_s": min(times),
            "max_s": max(times),
            "std_s": variance ** 0.5,
            "cycles_per_sec": r.cycles_used / mean_s if r.cycles_used > 0 else 0.0,
        }

    py_stats = _stats(py_times)
    if py_stats.get("cycles_per_sec"):
        py_stats["cycles_per_sec_label"] = f"{py_stats['cycles_per_sec']:,.0f} cyc/s"
    # Recalculate cycles_per_sec based on actual result
    _ = py.execute(bytecode)
    hz_py = r.cycles_used / py_stats["mean_s"] if r.cycles_used > 0 else 0.0
    py_stats["cycles_per_sec"] = round(hz_py, 1)

    result: dict[str, Any] = {"python": py_stats}

    if native_ok and native_times:
        nat = _stats(native_times)
        if nat.get("cycles_per_sec"):
            nat["cycles_per_sec_label"] = f"{nat['cycles_per_sec']:,.0f} cyc/s"
        hz_nat = r.cycles_used / nat["mean_s"] if r.cycles_used > 0 else 0.0
        nat["cycles_per_sec"] = round(hz_nat, 1)
        nat["available"] = True
        nat["speedup"] = (
            round(py_stats["mean_s"] / nat["mean_s"], 2)
            if nat["mean_s"] > 0
            else float("inf")
        )
        result["native"] = nat
    else:
        result["native"] = {"available": False}

    return result
