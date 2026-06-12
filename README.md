# flux_bridge — Python bridge to the FLUX ecosystem

*LLM Translation Engine + VM Harness + A2A Signal Router + Constraint Validator*

## Architecture Blueprint

### The Five-Layer Stack
```
open-parallel (orchestration)            ← Future
    ↓
pincher (reflex compilation)             ← Rust, supervised
    ↓
flux-core (VM + bytecode + A2A)         ← Rust, compiled ↗ PUFFER
    ↓
cuda-oxide (FLUX → PTX lowering)        ← Future (GPU needed)
    ↓
cudaclaw (safe GPU execution)           ← Future (GPU needed)
```

### The Bridge Layer (what we're building)
```
┌─────────────────────────────────────────────────┐
│                  LLM Agent                       │
│  (fleet-midi agents, character system)           │
└──────────────────┬──────────────────────────────┘
                   │ Natural language intent
                   ▼
┌─────────────────────────────────────────────────┐
│  flux_bridge/llm_engine.py                      │
│  LLM Translation & Constraint Engine            │
│  ┌────────────────────────────────────────┐      │
│  │ 1. Accept: NL intent + context         │      │
│  │ 2. Validate: schema check (Zod/Pydantic)│     │
│  │ 3. Translate: intent → FluxIR          │      │
│  │ 4. Assemble: FluxIR → bytecode         │      │
│  │ 5. Verify: opcode validator            │      │
│  └────────────────────────────────────────┘      │
└──────────────────┬──────────────────────────────┘
                   │ Validated bytecode
                   ▼
┌─────────────────────────────────────────────────┐
│  flux_bridge/vm_harness.rs/.py                  │
│  FFI / Sandbox Wrapper                          │
│  ┌────────────────────────────────────────┐      │
│  │ • Spawn flux-vm-v3 subprocess          │      │
│  │ • Execute bytecode with cycle limits   │      │
│  │ • Snapshot / restore state             │      │
│  │ • Debug hooks for tracing              │      │
│  └────────────────────────────────────────┘      │
└──────────────────┬──────────────────────────────┘
                   │ Execution results
                   ▼
┌─────────────────────────────────────────────────┐
│  flux_bridge/signal_router.py                   │
│  A2A Signal Protocol Router                     │
│  ┌────────────────────────────────────────┐      │
│  │ • A2AMessage (Tell/Ask/Offer/Ack/Fail) │      │
│  │ • Agent registry + mailbox             │      │
│  │ • State routing via conversation_id   │      │
│  │ • Character stat integration           │      │
│  └────────────────────────────────────────┘      │
└─────────────────────────────────────────────────┘
```

### Key Design Decisions

1. **Subprocess vs FFI**: Use compiled flux-core CLI binary + subprocess
   - Reason: Zero Rust build complexity for Python agents
   - ARM64 compatibility confirmed (Oracle2 is ARM64)
   - JSON over stdin/stdout for bytecode exchange

2. **A2A in Pure Python**: Implement A2AMessage in Python
   - Exact binary format match with flux-core (16-byte IDs, trust_score f32)
   - No Rust dependency for agent-to-agent communication
   - Fleet character integration (stats affect trust_score)

3. **Constraint Validation at the Boundary**:
   - Pydantic schemas intercept LLM output before VM execution
   - Opcode validator: known opcodes, register bounds, cycle limits
   - Zero-tolerance for invalid bytecode (reject, don't try to fix)

4. **Deterministic State Management**:
   - Register file snapshots for rollback
   - Conversation_id chains for message causality
   - Tick-based sequencing matching AgentCharacter.tick

## Module Structure (Phase 1-3)

```
flux_bridge/
├── README.md              ← This file
├── __init__.py            ← Exports
├── llm_engine.py          ← Phase 2: LLM Translation Engine
│   ├── translate(intent, context) → FluxIR
│   ├── validate(ir) → Result
│   └── few_shot_corpus() → examples
├── vm_harness.py          ← Phase 1: VM Harness
│   ├── build_vm() → path
│   ├── execute(bytecode, registers) → result
│   ├── snapshot() / restore(snapshot)
│   └── disassemble(bytecode) → asm
├── signal_router.py       ← Phase 3: A2A Signal Router
│   ├── A2AMessage — binary-compatible with flux-core
│   ├── AgentRegistry — named agents with mailboxes
│   ├── SwarmRouter — message routing with stats
│   └── Conversation — conversation_id chains
├── bytecode/              ← Bytecode utilities
│   ├── opcodes.py         — Op enum (matches Rust Op)
│   ├── assembler.py       — Assembly → bytecode
│   ├── disassembler.py    — Bytecode → assembly
│   └── validator.py       — Constraint validation
├── schemas/               ← Pydantic / JsonSchema models
│   ├── __init__.py
│   ├── messages.py        — A2A message schemas
│   └── constraints.py     — FLUX constraint schemas
├── prompts/               ← Phase 2 deliverable
│   ├── flux_alignment.md  — LLM alignment prompts
│   └── few_shot.json      — Few-shot corpus
└── tests/
    ├── test_vm.py         — VM harness tests
    ├── test_assembler.py  — Assembler roundtrip tests
    ├── test_signal.py     — A2A protocol tests
    └── performance_benchmark.py — Phase 3 deliverable
```
