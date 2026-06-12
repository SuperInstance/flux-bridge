# flux_bridge — Python Bridge to the FLUX Ecosystem

*LLM Translation Engine + VM Harness + A2A Signal Router + Constraint Validator*

## Quick Start

```bash
# From cloned repo
python3 -c "
from flux_bridge.bytecode import Assembler, Disassembler, validate
from flux_bridge.vm_harness import PythonInterpreter

a = Assembler()
a.emit_mov_i(0, 42)
a.emit_mov_i(1, 7)
a.emit_add(0, 1)
a.emit_halt()

code = a.assemble()
vr = validate(code)
print(f'Valid: {vr.safe}')  # True

result = PythonInterpreter().execute(code)
print(f'42 + 7 = {result.registers[0]}')  # 49
"
```

## Architecture

```
Agent Intent → Assembler → Bytecode → PythonInterpreter → Result
                                       ↓
                               SwarmRouter → A2AMessage → Agent Mailbox
                                       ↓
                              CharacterStore (SQLite)
```

## Modules

| Module | Lines | Purpose |
|--------|-------|---------|
| `bytecode/opcodes.py` | 238 | Op enum (38 opcodes matching flux-core Rust) |
| `bytecode/assembler.py` | 323 | Assembly → bytecode with label resolution |
| `bytecode/disassembler.py` | 273 | Bytecode → human-readable with offsets |
| `bytecode/validator.py` | 281 | Zero-tolerance constraint validation |
| `vm_harness.py` | 869 | PythonInterpreter — full opcode dispatch |
| `signal_router.py` | 881 | A2AMessage binary protocol + SwarmRouter |
| `fleet_adapter.py` | 350+ | Phase A hybrid HTTP+A2A adapter |
| `__init__.py` | lazy | Lazy-loading hub with health_check |

## Performance

| Metric | Value |
|--------|-------|
| VM throughput | **933,544 cycles/sec** |
| A2A throughput | **246,612 messages/sec** |
| Assemble latency | **7.6µs** per program |
| Validate latency | **~1.2ms** per program |
| PoC execution | **4ms** for 3-agent flow |

## API

### Bytecode
```python
from flux_bridge.bytecode import Op, Assembler, Disassembler, validate

# Opcodes
Op.MOVI, Op.IADD, Op.HALT  # 38 opcodes total
Op.is_alu(Op.MUL)           # True
Op.is_a2a(Op.TELL)          # True
Op.instruction_size(Op.MOVI) # 4

# Assembly
a = Assembler()
a.emit_mov_i(0, 42)     # MOVI R0, 42
a.emit_add(0, 1)        # IADD R0, R1
a.label("loop")         # Label for branch target
a.emit_jmp(-4)          # JMP (relative offset)
a.emit_halt()           # HALT
code = a.assemble()      # → bytes
hex_str = a.assemble_to_hex()  # → hex string

# Disassembly
instructions = Disassembler.disassemble(code)
text = Disassembler.disassemble_to_text(code)

# Validation
vr = validate(code)
vr.safe, vr.errors, vr.warnings
```

### VM
```python
from flux_bridge.vm_harness import PythonInterpreter

vm = PythonInterpreter()
result = vm.execute(code)
result.registers       # [42, 7, 0, ...]  (16 GP registers)
result.fp_registers    # 16 FP registers
result.cycles_used     # 4
result.halted          # True
result.success         # True
result.error           # None (or error message)
```

### A2A Signal Router
```python
from flux_bridge.signal_router import A2AMessage, MessageType, SwarmRouter

# Binary-compatible with flux-core Rust A2A (same wire format)
msg = A2AMessage(
    sender=b'agent-id'.ljust(16, b'\x00'),
    receiver=b'target-id'.ljust(16, b'\x00'),
    conversation_id=os.urandom(16),
    message_type=MessageType.Tell,  # Tell/Ask/Delegate/Broadcast
    payload=b'{"hello":"world"}',
    trust_score=0.85,
)
raw = msg.to_bytes()  # → 182 bytes binary
restored = A2AMessage.from_bytes(raw)

# Swarm routing
router = SwarmRouter()
router.register_agent('chord-1', 'ChordMaster', 'chord')
router.register_agent('melody-1', 'MelodySage', 'melody')
router.route(msg)
```

### Fleet Adapter
```python
from flux_bridge.fleet_adapter import FleetAdapter

adapter = FleetAdapter()  # 16 agents + SQLite persistence
agent = adapter.get_agent('chord')
result = agent.process_request('cue', True, 42)
status = adapter.status()  # All 16 agent profiles
adapter.save_all()
```

## CLI Scripts

```bash
# Run the fleet adapter server (port 2176)
python3 fleet_adapter.py

# Print agent statuses
python3 fleet_adapter.py --status

# Run the PoC demo
python3 demo/interop_poc.py
```

## Dependencies

Zero. Python stdlib only. No pip install needed.

## License

MIT
