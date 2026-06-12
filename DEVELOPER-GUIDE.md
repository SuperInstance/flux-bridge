# Developer Guide — flux_bridge

*For human engineers and visiting AI agents. Service-manual quality.*

## Design Philosophy

The flux_bridge is **not** a general-purpose virtual machine. It's a specialized
agent coordination runtime designed for one specific purpose: translating LLM
intents into deterministic, verifiable, cross-platform bytecode that 16
fleet-midi agents can execute.

### Key Decisions

1. **Python stdlib only** — Zero pip installs. The bridge runs on any Python 3.10+.
2. **Binary-compatible with Rust flux-core** — A2AMessage wire format matches the Rust implementation byte-for-byte. Python agents and Rust agents speak the same protocol.
3. **Fallback-first** — Pure Python interpreter means no Rust compilation needed. The Rust VM binary is an optimization, not a requirement.
4. **Constraint validation at every boundary** — LLM output → bytecode is intercepted by the validator before execution. Invalid bytecode is rejected, never fixed.

## Module Map

```
flux_bridge/
├── __init__.py              # Lazy-loading hub, health_check, status_report
├── bytecode/
│   ├── __init__.py          # Exports
│   ├── opcodes.py           # Op enum (38 values), from_byte, instruction_size
│   ├── assembler.py         # Assembler class with label resolution
│   ├── disassembler.py      # Disassemble bytes → instructions
│   └── validator.py         # Constraint validation (reject, don't fix)
├── vm_harness.py            # PythonInterpreter, VMResult, VMHarness
├── signal_router.py         # A2AMessage, SwarmRouter, AgentRegistry
├── llm_engine.py            # LLMTranslationEngine (Phase 2)
├── fleet_adapter.py         # Phase A hybrid HTTP+A2A adapter (Phase 1)
├── schemas/                 # Pydantic/Frankenstruct constraint schemas
├── prompts/
│   ├── flux_alignment.md    # LLM system prompt for bytecode generation
│   └── few_shot.json        # 5 high-quality bytecode examples
├── demo/
│   └── interop_poc.py       # Multi-agent A2A scenario (3 agents, 4ms)
├── tests/
│   ├── test_vm.py           # 12 VM tests
│   ├── test_assembler.py    # 51 assembler tests
│   ├── test_signal.py       # 33 A2A signal tests
│   ├── performance_benchmark.py
│   └── BENCHMARK-REPORT.md
└── ECOSYSTEM-SYNTHESIS.md   # Cross-crate analysis of 8 FLUX repos
```

## Instruction Encoding

Every FLUX instruction is 2-4 bytes:

```
[opcode(1)] [reg(1)] [optional: reg2(1)] [optional: imm(2)]
```

### 1-byte instructions
`NOP(0x00), RET(0x28), DUP(0x22), HALT(0x80), YIELD(0x81)`

### 3-byte instructions (opcode + reg + reg)
`MOV(0x01), IADD(0x08)..DEC(0x0F), PUSH(0x20), POP(0x21), CMP(0x2D)`
`IAND(0x10)..INOT(0x13), ISHL(0x14), ISHR(0x15)`

### 4-byte instructions (opcode + reg + imm16)
`MOVI(0x2B), JMP(0x04), JZ(0x05), JNZ(0x06), CALL(0x07)`

### Note on A2A instructions
`TELL(0x60), ASK(0x61), DELEGATE(0x62), BROADCAST(0x66)`
These are variable-length: opcode(1) + payload_len(2) + payload(N).

## Registers

| Range | Type | Count | Purpose |
|-------|------|-------|---------|
| R0-R15 | GP (i32) | 16 | Integer operations, addresses |
| F16-F31 | FP (f32) | 16 | Floating-point operations |

Register R16 in a GP instruction is invalid (caught by validator).

## How to Add a New Opcode

1. Add to `bytecode/opcodes.py` Op enum:
```python
MY_OP = 0x90
```

2. Add `instruction_size` entry:
```python
0x90: 3,  # opcode + 2 regs
```

3. Add `is_alu` / `is_control_flow` classifications if applicable.

4. Add to `bytecode/assembler.py` Assembler:
```python
def emit_my_op(self, dst: int, src: int):
    self._validate_reg(dst, self.GP_RANGE)
    self._validate_reg(src, self.GP_RANGE)
    self._bytes.extend([Op.MY_OP, dst, src])
```

5. Add to `vm_harness.py` PythonInterpreter `_execute_instruction`:
```python
if op == 0x90:
    dst = self._read_byte()
    src = self._read_byte()
    self.regs[dst] = self.regs[dst] OP self.regs[src]
```

6. Add to `bytecode/disassembler.py`:
```python
0x90: lambda offset, op, regs: "MY_OP R{}, R{}".format(regs[0], regs[1]),
```

## Bytecode Program Patterns

### Arithmetic
```python
a = Assembler()
a.emit_mov_i(0, 3)    # R0 = 3
a.emit_mov_i(1, 4)    # R1 = 4
a.emit_add(0, 1)      # R0 = R0 + R1 = 7
a.emit_halt()
```

### Loop
```python
a = Assembler()
a.emit_mov_i(0, 5)    # counter = 5
a.label("loop")
a.emit_dec(0)         # counter--
a.emit_jnz(0, "loop") # if counter != 0, loop
a.emit_halt()
```

### Agent Communication
```python
a = Assembler()
a.emit_mov_i(0, ord('r'))  # 'r' = 114
a.emit_tell(0)              # TELL R0 (sends byte as payload)
a.emit_halt()
```

## Integration with Fleet Characters

Each agent character (AgentCharacter from fleet-characters/) updates stats
from every request processed. The `fleet_adapter.py` automatically:

1. **Loads** character from SQLite on startup
2. **Updates** stats on every request (perception, dexterity, intelligence, etc.)
3. **Saves** to SQLite every 30 seconds or 10 requests
4. **Triggers** dream cycles when total_requests % 100 == 0

The A2A trust_score is influenced by:
- Charisma > 15 → +0.2 trust boost
- Wisdom > 15 → more trust for Ask messages  
- Constitution > 15 → slower trust decay
- Integration score → affects Broadcast trust

## Testing

```bash
# Run all tests
python3 -m pytest tests/ -v
python3 -m unittest discover tests/ -v

# Run benchmarks
python3 tests/performance_benchmark.py

# Run the PoC demo
python3 demo/interop_poc.py
```

## Deployment Scripts

Planned CLI tools (Phase B):
- `fluxd` — Fleet daemon running all 16 agents
- `flux-run` — One-shot bytecode runner  
- `flux-teach` — Intent → bytecode compilation

These live in `bin/` once written.

## Related

- [flux-core](https://github.com/SuperInstance/flux-core) — Rust VM, 29 tests
- [fleet-characters](https://github.com/SuperInstance/fleet-characters) — Agent identity system
- [pincher-flux-bridge](https://github.com/SuperInstance/pincher-flux-bridge) — Reflex to FluxIR
- [fleet-agent](https://github.com/SuperInstance/fleet-agent) — 16 agent launcher
