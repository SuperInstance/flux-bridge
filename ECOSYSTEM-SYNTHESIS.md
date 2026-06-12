# FLUX Ecosystem — Cross-Crate Architectural Synthesis

> Generated: 2026-06-12  
> Scope: 8 core repositories across the SuperInstance org  
> Purpose: Integration blueprint for the Python `flux_bridge`

---

## 1. Crate Dependency Graph

```
                        ┌─────────────────────┐
                        │   pincher-flux-bridge │
                        │   (reflex→FluxIR)    │
                        └──────┬──────────────┘
                               │ FluxIR instructions
                               ▼
┌──────────────────────────────────────────────────────────┐
│                   flux-core                               │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐          │
│  │  vm/       │  │ bytecode/  │  │ a2a/       │          │
│  │ interpreter│  │ assembler  │  │ messages   │          │
│  │ registers  │  │ disassemblr│  │ swarm      │          │
│  └─────┬──────┘  └─────┬──────┘  └──────┬─────┘          │
│  ┌──────┴──────┐  ┌─────┴──────┐       │                 │
│  │ vocabulary/ │  │ error.rs   │       │                 │
│  └─────────────┘  └───────────┘       │                 │
└──────────────────────┬─────────────────┘                  │
                       │ A2A compile_request                 │
                       ▼                                     │
┌────────────────────────────────────────────────┐          │
│            plato-flux-compiler                  │          │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐       │          │
│  │ ast.rs  │  │ parser.rs │  │ codegen.rs│      │          │
│  └────┬────┘  └─────┬────┘  └─────┬──────┘      │          │
│  ┌────┴────┐  ┌─────┴──────┐  ┌────┴────┐      │          │
│  │ optimizer│  │integration │  │examples │      │          │
│  └─────────┘  └────────────┘  └─────────┘      │          │
└──────────────────────┬─────────────────────────┘          │
                       │ FLUX bytecode (compiled)            │
                       ▼                                     │
┌────────────────────────────────────────────────┐          │
│   cuda-oxide  (Rust → PTX, 18 crates, 124K LOC)│          │
│  flux-importer → MIR → Pliron → NVVM → LLVM → PTX        │
│  ┌──────────┐  ┌──────────┐  ┌────────────────┐          │
│  │llvm-exp. │  │  fuzzer  │  │  PTX backend   │          │
│  └──────────┘  └──────────┘  └────────────────┘          │
└──────────────────────┬────────────────────────────────────┘
                       │ PTX binary                          │
                       ▼                                     │
┌────────────────────────────────────────────────┐          │
│                cudaclaw                          │          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │          │
│  │persistent│  │dispatcher│  │  lock_free    │   │          │
│  │ kernel   │  │ GpuDisp  │  │  queue       │   │          │
│  └──────────┘  └──────────┘  └──────────────┘   │          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │          │
│  │ bridge   │  │gpu_metr. │  │  SpinLockDisp│   │          │
│  └──────────┘  └──────────┘  └──────────────┘   │          │
└──────┬───────────────────────────────────────────┘         │
       │ GPU command dispatch                                 │
       ▼                                                     │
┌────────────────────────────────────────────────┐          │
│         flux-autoscale                          │          │
│  Monitors GPU streams, adjusts parallelism      │          │
│  Controls how many cudaclaw streams are active  │          │
└────────────────────────────────────────────────┘          │

       ┌────────────────────────────────────────┐
       │         ternary-svm                     │
       │  Standalone: no crate deps to flux core │
       │  Ternary ML for agent decision-making   │
       │  Predicts labels {-1, 0, +1}            │
       └────────────────────────────────────────┘

```

### Dependency Edges (explicit Cargo.toml deps)

| Crate | Depends On | Exports To | Data Direction |
|-------|-----------|-----------|----------------|
| `flux-core` | `regex` (std only) | plato, pincher-bridge, cuda-oxide | Bytecode + A2A msg |
| `pincher-flux-bridge` | (none) | flux-core | `FluxIR` instructions → VM |
| `plato-flux-compiler` | `flux-core` (git) | cuda-oxide | Compiled bytecode |
| `cuda-oxide` | 18 internal crates | cudaclaw | PTX blob |
| `cudaclaw` | `cust` (CUDA Rust), `serde` | GPU hardware | Unified memory commands |
| `flux-autoscale` | (none standalone) | cudaclaw | ScaleAction control |
| `ternary-svm` | `ternary-types` (git) | (standalone) | ML predictions |
| `flux-vm-dispatch` | `tokio`, `serde`, `flux-core` (git) | cudaclaw | Async dispatch |

---

## 2. Data Flow Through the Five-Layer Stack

```
 open-parallel ──→ pincher ──→ flux-core ──→ cuda-oxide ──→ cudaclaw
 (orchestration)   (reflex)    (VM + A2A)    (PTX lowering)  (GPU exec)
```

### Layer 1: open-parallel (Orchestration) — *Not in studied repos*

| Property | Detail |
|----------|--------|
| **Role** | Async executor, event loop, I/O multiplexer (tokio fork) |
| **Consumes** | User/agent intents (JSON intent + tensor metadata) |
| **Produces** | Scheduled async tasks; validated command tokens |
| **Key Interface** | `async fn submit_intent(intent) -> Result<PtxModule>` |
| **Error Strategy** | Standard async error handling via `?` + timeout futures |
| **Note** | Not in studied repos; described in SYNERGY_ANALYSIS.md |

### Layer 2: pincher (Reflex Compilation)

| Property | Detail |
|----------|--------|
| **Role** | Semantic router; LLM maps intent → Flux bytecode |
| **Consumes** | `Reflex { intent, action, confidence, invoke_count }` from .nail bundles |
| **Produces** | `FluxIR` instructions (`MatchIntent→ConditionalExec→Halt` triples) |
| **Key Interface** | `reflex_to_flux(bundle, threshold) -> (Vec<FluxIR>, ConversionFidelity)` |
| **Error Strategy** | Confidence-threshold filtering (skip < 0.5); `ConversionFidelity` tracks skipped/unsupported |
| **Data Types** | `Trit` (i8 in {-1,0,1}), `Reflex`, `NailBundle`, `FluxIR` (10 variants) |

**Data type consumed → produced:**
```
NailBundle { agent_name, reflexes: Vec<Reflex> }
  → reflex_to_flux(threshold) →
Vec<FluxIR>  (MatchIntent → ConditionalExec → Halt triples)
  + ConversionFidelity { total, converted, skipped_low, fidelity_ratio }
```

### Layer 3: flux-core (VM + A2A)

| Property | Detail |
|----------|--------|
| **Role** | Portable bytecode VM, assembler/disassembler, A2A agent protocol |
| **Consumes** | Assembly text → `assemble()` → bytecode `Vec<u8>`; or `FluxIR` instructions |
| **Produces** | Execution results (register values, stack), dispatched A2A messages |
| **Key Interfaces** | `Assembler::assemble(text, init_pc)`, `Interpreter::run()`, `A2AMessage` serialization |
| **Error Strategy** | `FluxError` enum with 8+ variants: Halt, StackUnderflow, InvalidOpcode, DivByZero, etc. |

**Consumed → Produced:**
```
Assembly text (labels, opcodes, immediates)
  → Assembler (2-pass) → bytecode Vec<u8>
  → Interpreter → Result<(), FluxError> (registers updated, A2A messages dispatched)

A2A messages (63+ N bytes wire format):
  UUID sender | UUID receiver | conv_id | msg_type | length | payload | trust_score | timestamp
```

### Layer 4: cuda-oxide (PTX Lowering)

| Property | Detail |
|----------|--------|
| **Role** | Rust → PTX compiler; Flux bytecode → synthetic MIR → Pliron → NVVM → LLVM → PTX |
| **Consumes** | Flux bytecode (via `flux-importer`); Rust MIR (native path) |
| **Produces** | PTX binary strings (loaded via `cuModuleLoadData`) |
| **Key Interface** | `flux-importer` translation: `FluxToMir::translate(bytecode) -> StableMir` |
| **Error Strategy** | LLVM diagnostic pass-through; type inference errors captured at MIR generation |
| **Scale** | 124K LOC, 18 internal crates, production-quality LLVM pipeline |

**Consumed → Produced:**
```
Flux bytecode or Rust MIR
  → flux-importer (FluxToMir::translate)
  → Stable MIR → Pliron IR → NVVM IR → LLVM IR
  → PTX text (target: sm_80/sm_86/sm_89/sm_90)
```

### Layer 5: cudaclaw (GPU Execution)

| Property | Detail |
|----------|--------|
| **Role** | Persistent GPU kernel runtime; unified-memory command dispatch |
| **Consumes** | PTX binary → `cuModuleLoadData`; `Command` structs (48 bytes each) |
| **Produces** | GPU computation results (in unified memory `Command.result`); latency metrics |
| **Key Interface** | `CudaClawExecutor::new(variant)`, `push_command()`, `launch_kernel()`, `stop_kernel()` |
| **Error Strategy** | CUDA error propagation via `cust::error::CudaError`; spin-lock backpressure on queue full |

**Consumed → Produced:**
```
PTX string → CudaClawExecutor::new(KernelVariant)
  → launch_kernel() → GPU persistent worker running
  → Command { cmd_type, id, data_a, data_b } → push_command()
  → GPU processes → Command.result filled → dispatch collected
```

### Cross-Layer Error Propagation

```
pincher (skipped reflexes) → flux-core (FluxError) → cuda-oxide (LLVM diag) → cudaclaw (CudaError)
         ↓                    ↓                      ↓                        ↓
  ConversionFidelity    FluxError enum           CompileError            CudaError + latency
  (soft skip)          (Halt, Div0, OOB)        (type mismatch)          (OOM, timeout)
```

---

## 3. Compiler Constraint Matrix

### VM Constraints

| Constraint | Value | Source | Enforced By |
|------------|-------|--------|-------------|
| GP registers | 16 (R0-R15), 32-bit | `registers.rs` | Runtime bounds check in `decode` |
| FP registers | 16 (F0-F15), 64-bit | `registers.rs` | Runtime bounds check in `decode` |
| SIMD registers | 16 (V0-V15), 128-bit | `registers.rs` (v2) | Runtime bounds check |
| PC | 1 × 32-bit | `registers.rs` | Internal |
| Stack pointer | 1 × 32-bit | `registers.rs` | Internal |
| Cycle limit (v1) | 10,000,000 | `interpreter.rs` | `run()` loop counter |
| Max instructions (v2) | configurable | `interpreter.rs` | `run_with_budget()` |
| Memory page size | 4 KB | `interpreter.rs` v2 | Allocation alignment |
| Default memory | 64 KB | `interpreter.rs` v2 | `vm_new()` |
| Max memory | 16 MB | `interpreter.rs` v2 | Allocation bounds check |
| Stack max depth | ~8 KB (2048 i32 values) | implicit | StackUnderflow on POP/STORE |
| Instruction alignment | 1-byte (variable length) | design | None (must decode at PC) |

### Bytecode Constraints

| Constraint | Value | Source |
|------------|-------|--------|
| Valid opcodes | 0x00, 0x01-0x15, 0x20-0x22, 0x28, 0x2B, 0x2D, 0x40-0x43, 0x60-0x62, 0x66, 0x80-0x81 | `opcodes.rs` |
| Opcode range | 0x00-0x81 (with gaps) | `opcodes.rs` (247 target, 30 implemented) |
| Format A size | 1 byte | `opcodes.rs` |
| Format B size | 2 bytes | `opcodes.rs` |
| Format C size | 3 bytes | `opcodes.rs` |
| Format D size | 4 bytes | `opcodes.rs` |
| Format E size | 4 bytes | `opcodes.rs` |
| Format G size | 3 + payload_length | `opcodes.rs` |
| Label resolution | PC-relative i16 offset | `assembler.rs` |
| Immediate range | -32768..=32767 (i16, sign-extended to i32) | `assembler.rs` |
| Register indices | 0-15 (4-bit) | implicit in formats B-E |
| Disassembler fallback | `(unknown opcode {hex})` | `disassembler.rs` |
| Truncated instruction | `(truncated)` | `disassembler.rs` |

### A2A Message Constraints

| Constraint | Value | Source |
|------------|-------|--------|
| Minimum wire size | 63 bytes (v2), 55 bytes (v1) | `messages.rs` |
| Sender UUID | 16 bytes | `messages.rs` |
| Receiver UUID | 16 bytes | `messages.rs` |
| Conversation ID | 16 bytes | `messages.rs` |
| Message type | u8 (1-4: Tell, Ask, Delegate, Broadcast) | `messages.rs` |
| Payload length field | u16 | `messages.rs` |
| Maximum payload | 65,535 bytes (by u16 length field) | implicit |
| Trust score | f32, range 0.0-1.0 | `messages.rs` |
| Default trust score | 1.0 | `swarm.rs` |
| Timestamp | u64 (v2 only), micros since epoch | `messages.rs` |
| Endianness (v1) | Little-endian | `messages.rs` |
| Endianness (v2) | Big-endian | `messages.rs` |

> **⚠️ CRITICAL**: v1 and v2 are **wire-incompatible** (different endianness for multi-byte fields).

### Vocabulary Constraints

| Constraint | Value | Source |
|------------|-------|--------|
| Pattern format | Rust regex | `vocabulary/interpreter.rs` |
| Capture groups | {0}, {1}, ... substituted into assembly template | `vocabulary/interpreter.rs` |
| Assembly template | Must be valid FLUX assembly | `vocabulary/interpreter.rs` |
| Result register | Must be valid register (R0-R15, F0-F15) | `vocab_entry.result_reg` |
| Vocabulary entries | Unbounded (hashmap-based) | `vocabulary/mod.rs` |
| Matching strategy | First-match wins (iterate in insertion order) | `vocabulary/interpreter.rs` |

### Architecture Constraints

| Constraint | Detail |
|------------|--------|
| **ARM64** | FLUX core bytecode: architecture-agnostic; interpreter runs anywhere Rust runs |
| **x86_64** | FLUX core bytecode: same as ARM64; no arch-specific branch in interpreter |
| **GPU requirement** | cudaclaw requires NVIDIA GPU with CUDA compute capability (minimum 6.0+, recommended 8.0+) |
| **GPU SM targets** | sm_80 (Ampere A100, RTX 3090), sm_86 (GA102), sm_89 (Ada), sm_90 (H100 Hopper) |
| **CPU-only fallback** | flux-core interpreter runs on CPU; no GPU needed for bytecode-only execution |
| **NVML** | cudaclaw GPU metrics: optional, graceful fallback when unavailable |
| **cuda-oxide LLVM** | Requires LLVM 19.x; CUDA SDK 12.4+ recommended |
| **Memory model** | cudaclaw uses unified memory (host+device unified addressing); no explicit cudaMemcpy needed |
| **Host pointer** | `CommandQueueHost` layout must match C++ CUDA definition exactly (49,192 bytes verified at compile time) |

---

## 4. API Surface Map

### 4.1 flux-core

**Module: `vm/`** — Stable

| Function/Type | Signature | Notes |
|--------------|-----------|-------|
| `Registers` | struct (gp, fp, simd, pc, sp, fp, lr, flags) | Public fields |
| `Registers::new()` | `-> Self` | Default zero-initialized |
| `Registers::read_gp(r: u8)` | `-> Result<i32, FluxError>` | Bounds check 0-15 |
| `Registers::write_gp(r: u8, v: i32)` | `-> Result<(), FluxError>` | Bounds check 0-15 |
| `Registers::read_fp(r: u8)` | `-> Result<f64, FluxError>` | Bounds check 0-15 |
| `Registers::write_fp(r: u8, v: f64)` | `-> Result<(), FluxError>` | Bounds check 0-15 |
| `Registers::flags()` | `-> Flags` | Accessor |
| `Interpreter` | struct (registers, memory, stack, a2a queues, cycle_budget) | Core VM |
| `Interpreter::new()` | `-> Self` | Default 64KB memory |
| `Interpreter::with_memory(sz)` | `-> Self` | Configurable size |
| `Interpreter::load_program(code)` | `-> Result<(), FluxError>` | Load into code segment |
| `Interpreter::step()` | `-> Result<(), FluxError>` | Single instruction |
| `Interpreter::run()` | `-> Result<(), FluxError>` | Until HALT or error |
| `Interpreter::run_with_budget(n)` | `-> Result<(), FluxError>` | Instruction budget |
| `Interpreter::reset()` | `-> ()` | Reset state |

**Module: `bytecode/`** — Stable

| Function/Type | Signature | Notes |
|--------------|-----------|-------|
| `Opcodes::from_u8(b: u8)` | `-> Option<Opcode>` | 30 opcodes implemented |
| `Assembler::assemble(text)` | `-> Result<Vec<u8>, String>` | Two-pass, label resolution |
| `Assembler::assemble_at(text, pc)` | `-> Result<Vec<u8>, String>` | With offset |
| `Disassembler::disassemble(code)` | `-> Vec<DisassembledInstruction>` | Full program |
| `Disassembler::format(code)` | `-> String` | Human-readable text |
| `DisassembledInstruction` | struct (offset, opcode, text, size) | Public fields |

**Module: `a2a/`** — Experimental

| Function/Type | Signature | Notes |
|--------------|-----------|-------|
| `A2AMessage` | struct (sender, receiver, conv_id, msg_type, payload, trust_score, timestamp) | v2 has timestamp field |
| `A2AMessage::new(s, r, t, p)` | `-> Self` | Constructor |
| `A2AMessage::serialize()` | `-> Vec<u8>` | Wire format |
| `A2AMessage::deserialize(buf)` | `-> Option<A2AMessage>` | Big-endian (v2) or LE (v1) |
| `Swarm` | struct (agents: HashMap<String, Agent>) | v1 only |
| `Swarm::new()` | `-> Self` | Constructor |
| `Swarm::tick()` | `-> u32` | Total cycles all agents |
| `Swarm::vote(reg)` | `-> HashMap<i32, u32>` | Frequency count |
| `Swarm::consensus(reg)` | `-> i32` | Majority value |
| `Agent` | struct (id, role, trust, inbox, generation, bytecode) | |

**Module: `vocabulary/`** — Experimental

| Function/Type | Signature | Notes |
|--------------|-----------|-------|
| `Vocabulary` | struct (entries: Vec<VocabEntry>) | |
| `Vocabulary::new()` | `-> Self` | Constructor |
| `Vocabulary::add_entry(entry)` | `-> ()` | Append new entry |
| `VocabEntry` | struct (pattern, assembly, result_reg, name) | |
| `VocabEntry::new(pattern, asm, reg, name)` | `-> Self` | Constructor |
| `Vocabulary::interpret(text)` | `-> Result<i32, String>` | Match → assemble → execute |

**Module: `error.rs`** — Stable

| Variant | Meaning |
|---------|---------|
| `FluxError::Halted` | Normal termination |
| `FluxError::InvalidOpcode(u8)` | Unknown opcode |
| `FluxError::StackUnderflow` | POP on empty stack |
| `FluxError::DivByZero` | IDIV/FDIV by zero |
| `FluxError::MemoryOverflow` | Memory out of bounds |
| `FluxError::RegBoundError` | Register index > 15 |
| `FluxError::UnsupportedFormat` | Format not yet implemented |
| `FluxError::A2AError(String)` | A2A message failure |
| `FluxError::VocabularyError(String)` | Vocab pattern/match error |

### 4.2 pincher-flux-bridge

**Module: lib.rs** — Stable

| Function/Type | Signature | Notes |
|--------------|-----------|-------|
| `Trit` | type alias `i8` | Only -1, 0, 1 are valid |
| `Reflex` | struct (intent, action, confidence, invoke_count) | |
| `NailBundle` | struct (agent_name, reflexes) | |
| `FluxIR` | enum: 10 variants | Push, Add, Mul, Load, Store, MatchIntent, ConditionalExec, BranchIf, Halt, Nop |
| `ConversionFidelity` | struct (total, converted, skipped_low, skipped_unsupported, fidelity_ratio) | |
| `reflex_to_flux(bundle, threshold)` | `-> (Vec<FluxIR>, ConversionFidelity)` | Main bridge function |
| `flux_to_teach(instructions)` | `-> Vec<Reflex>` | Round-trip back |
| `z3_add(a, b)` | `-> Trit` | Explicit 9-case match |
| `z3_mul(a, b)` | `-> Trit` | Sign product |
| `ConversionFidelity::perfect(n)` | `-> Self` | Convenience for 100% fidelity |

### 4.3 plato-flux-compiler

**Module: lib.rs** — Experimental

| Function/Type | Notes |
|--------------|-------|
| `ASTNode` enum | Program, Function, Assignment, BinaryOp, UnaryOp, Literal, Condition, Loop, etc. |
| `Parser::parse(source)` | Tokenize + parse to AST |
| `CodeGen::compile(ast)` | AST → FLUX bytecode (assembler instructions) |
| `Optimizer::optimize(bytecode)` | Peephole optimizations: constant folding, dead code elimination, strength reduction |
| `Integration::load_and_run(bytecode)` | Load compiled code into flux-core VM and execute |
| `Integration::flux_to_ptx_pipeline(bytecode, config)` | Compile bytecode via cuda-oxide |
| `ImportConfig` | struct with compute_capability, max_registers, GPU opts |

### 4.4 flux-vm-dispatch

**Module: lib.rs** — Experimental

| Function/Type | Notes |
|--------------|-------|
| `AsyncVmExecutor` | Tokio-based async VM execution |
| `AsyncVmExecutor::new(vm)` | Constructor |
| `AsyncVmExecutor::execute_async(bytecode)` | `-> impl Future<Output=Result<_,_>>` |
| `AsyncVmExecutor::execute_batch(batch)` | Concurrent batch execution |
| `DispatchMetrics` | struct with latency, throughput, concurrency stats |

### 4.5 ternary-svm

**Module: lib.rs** — Stable

| Function/Type | Notes |
|--------------|-------|
| `Trit` | type alias `i8` |
| `validate_ternary(vec)` | `-> Result<(), String>`; validates all elements ∈ {-1, 0, 1} |
| `Kernel` | enum: Linear, TernaryPolynomial, TernaryRBF |
| `Kernel::apply(a, b)` | `-> f64` |
| `BinarySVM` | struct: support vectors, alphas, bias |
| `BinarySVM::new(kernel, c)` | Constructor; C is SVM regularization |
| `BinarySVM::fit(x, y, max_passes)` | Simplified SMO |
| `BinarySVM::predict(x)` | `-> Result<f64, String>`; returns ±1 |
| `BinarySVM::decision_function(x)` | `-> f64`; raw margin |
| `BinarySVM::margin()` | `-> f64` |
| `BinarySVM::support_vectors()` | `-> &[usize]` |
| `TernarySVM` | One-vs-rest classifier for {-1, 0, +1} |
| `TernarySVM::new(kernel, c)` | Constructor |
| `TernarySVM::fit(x, y, max_passes)` | Trains 3 binary SVMs |
| `TernarySVM::predict(x)` | `-> Result<i8, String>` |

### 4.6 cudaclaw

**Module: cuda_claw.rs** — Experimental (cuda feature-flagged)

| Function/Type | Notes |
|--------------|-------|
| `CommandType` | enum: NoOp, Add, Subtract, Multiply, Divide, MatrixMultiply, MemoryCopy, Custom, SetCellValue, SpreadsheetEdit |
| `Command` | repr(C, packed(4)), 48 bytes. Fields: cmd_type(u32), id(u32), timestamp(u64), data_a(f64), data_b(f64), result(f64), batch_data(u64) |
| `Command::new(cmd_type, id)` | Constructor |
| `Command::with_data(a, b)` | Builder |
| `QueueStatus` | enum: Idle, Ready, Processing, Error (repr(u32)) |
| `CommandQueueHost` | repr(C, packed(8)), 49,192 bytes. Fields: buffer[1024], status, head, tail, is_running, commands_sent, commands_processed |
| `KernelVariant` | enum: Baseline, L1Preferred, ShmemPreferred, L1Equal, Unroll(u32), IdleSleep(u32), WarpAggregatedCas, SoaLayout, L1CachePref(u32), SharedMemory(u32), BlockSize(u32) |
| `CudaClawExecutor` | struct with Module, UnifiedBuffer, Stream |
| `CudaClawExecutor::new(variant)` | `-> Result<Self, CudaError>`; loads PTX |
| `CudaClawExecutor::launch_kernel()` | Launch persistent kernel |
| `CudaClawExecutor::stop_kernel()` | Signal+wait for shutdown |
| `CudaClawExecutor::push_command(cmd)` | `-> bool`; thread-safe queue push |
| `CudaClawExecutor::get_queue_mut()` | `-> &mut CommandQueueHost` |
| `CudaClawExecutor::execute_add(a, b)` | `-> Result<(Duration, f64), CudaError>`; convenience |
| `CudaClawExecutor::measure_warp_metrics()` | Warp utilization stats |
| `CudaClawExecutor::get_worker_stats()` | Worker statistics |
| `CudaClawExecutor::shutdown()` | Cleanup |

**Module: dispatcher.rs** — Experimental

| Function/Type | Notes |
|--------------|-------|
| `GpuDispatcher` | Mutex-based dispatcher for unified memory queue |
| `GpuDispatcher::new(queue, timeout_ms)` | Constructor |
| `GpuDispatcher::dispatch_sync(cmd)` | Blocking dispatch |
| `GpuDispatcher::dispatch_batch(cmds)` | Batch dispatch |
| `GpuDispatcher::get_stats()` | `-> DispatchStats` |
| `SpinLockDispatcher` | Atomic lock-free dispatcher (~50-100ns dispatch) |
| `SpinLockDispatcher::new(queue_ptr)` | Constructor |
| `SpinLockDispatcher::dispatch_atomic(cmd)` | `-> (cmd_id, latency_ns)` |
| `SpinLockDispatcher::benchmark_dispatch_to_execution(config)` | Full benchmark |
| `LockFreeDispatcher` | Relaxed-atomic, zero-sync dispatcher |
| `LockFreeDispatcher::new(queue_ptr)` | Constructor |
| `LockFreeDispatcher::push(cmd)` | `-> (cmd_id, latency_ns)` |
| `LockFreeDispatcher::stop()` | Set `is_running = false` |
| `BenchmarkConfig` | struct: num_commands, warmup, target_latency, cmd_type |
| `BenchmarkResult` | struct: comprehensive latency statistics |

**Module: bridge.rs** — Experimental

| Function/Type | Notes |
|--------------|-------|
| `GpuBridge<T: DeviceCopy>` | Unified memory allocator wrapper |
| `GpuBridge::init()` | `-> CudaResult<Self>`; allocates T |
| `GpuBridge::with_value(data)` | Init with specific value |
| `GpuBridge::as_device_ptr()` | `-> *mut T` for CUDA kernels |
| `allocate_command_queue()` | `-> (GpuBridge, *mut CommandQueueHost)` |
| `GpuBridgeBuilder<T>` | Builder pattern |

**Module: lock_free_queue.rs** — Stable

| Function/Type | Notes |
|--------------|-------|
| `LockFreeCommandQueue` | Static methods for queue ops |
| `LockFreeCommandQueue::push_command(queue, cmd)` | `-> bool` |
| `LockFreeCommandQueue::push_commands_batch(queue, cmds)` | `-> u32` |
| `LockFreeCommandQueue::wait_for_space(queue, cmd, max_spins)` | `-> bool` |
| `LockFreeSPSC<T>` | SPSC wrapper with volatile ops (~2-5ns) |

**Module: gpu_metrics.rs** — Stable

| Function/Type | Notes |
|--------------|-------|
| `GpuMetricsCollector` | NVML-based GPU monitor |
| `GpuMetricsSnapshot` | Temperature, utilization, power, clocks, throttling |
| `HighResolutionTimer` | Nanosecond-precision timer |
| `LatencyStats` | Statistical latency analysis |

### 4.7 cuda-oxide (llvm-export crate)

**Module: export/** — Experimental (Stable for core paths)

| Function/Type | Notes |
|--------------|-------|
| `Config` | LLVM compilation configuration |
| `Namespace` | Name mangling and resolution |
| `Function` | Function export to LLVM/PTX |
| `Module` | Module-level compilation |
| `Externs` | External symbol resolution |
| `State` | Compiler state management |
| `Types` | Type system mapping |
| `Literals` | Constant/literal lowering |
| `Ops` | Operation lowering to LLVM IR |
| `Metadata` | Debug info and metadata |
| `load_ptx(name)` | `-> Result<String, _>`; load compiled PTX |

### 4.8 flux-autoscale

**Module: lib.rs** — Stable

| Function/Type | Notes |
|--------------|-------|
| `ScaleAction` | enum: ScaleUp, ScaleDown, Hold |
| `StreamMetrics` | struct: stream_id, queue_depth, throughput, latency, backpressure |
| `Autoscaler` | struct: min/max/current streams, thresholds, events |
| `Autoscaler::new(min, max)` | Constructor |
| `Autoscaler::update_metrics(metrics)` | Feed current stream stats |
| `Autoscaler::evaluate()` | `-> ScaleAction` |
| `Autoscaler::run_ticks(tick_metrics)` | `-> Vec<ScaleAction>` |
| `Autoscaler::current_streams()` | `-> u32` |
| `Autoscaler::scale_events()` | `-> &[ScaleEvent]` |
| `Autoscaler::is_scaled_up()` | `-> bool` |
| `ScaleEvent` | struct: action, from, to, reason |

---

## 5. Integration Points for flux_bridge (Python Bridge)

### 5.1 flux-core — HIGH PRIORITY INTEGRATION

| Aspect | Strategy | Details |
|--------|----------|---------|
| **Assembler** | **PyO3 FFI** (performance critical) | Assembler is 2-pass — Python reimplementation would be slow and error-prone. Bind `assemble()` directly. |
| **Disassembler** | **PyO3 FFI** (performance critical) | Binary → text; needed for debug output. Bind directly. |
| **Interpreter** | **Subprocess** OR PyO3 | The interpreter has no external deps (no CUDA). Can run as a subprocess binary with stdin/stdout. PyO3 recommended for latency-sensitive paths. |
| **A2A messages** | **Python reimplementation** (safe) | Simple serialization format (fixed fields + payload). Easy to implement in pure Python; no performance bottleneck. |
| **Vocabulary** | **Python reimplementation** (easy) | Regex + template → easy in Python. Could even be more flexible than the Rust version. |
| **Fallback strategy** | Pure Python bytecode interpreter | A simplified stack-based interpreter in Python (no registers, just stack). Slower but works without Rust. |

**Recommended bindings:**

```python
# flux_bridge/flux_core.py
class FluxAssembler:
    """Thin wrapper around Rust assembler via PyO3."""
    def assemble(self, text: str) -> bytes: ...
    def assemble_at(self, text: str, pc: int) -> bytes: ...

class FluxDisassembler:
    def disassemble(self, code: bytes) -> list[DisassembledInstruction]: ...
    def format(self, code: bytes) -> str: ...

class FluxVM:
    def load_program(self, code: bytes): ...
    def run(self) -> None: ...
    def read_register(self, reg: str) -> int | float: ...
```

### 5.2 pincher-flux-bridge — NATURAL FOR PYTHON

| Aspect | Strategy | Details |
|--------|----------|---------|
| **Entire crate** | **Python reimplementation** | Pure data conversion: `NailBundle` → `FluxIR`. No performance bottleneck. Z₃ arithmetic is trivial. |
| **Z₃ arithmetic** | **Python reimplementation** | 9-case match table, trivially replicable. |
| **Fallback** | Same (already pure Python compatible) | This IS the fallback — no Rust needed at all. |

### 5.3 plato-flux-compiler — MIXED STRATEGY

| Aspect | Strategy | Details |
|--------|----------|---------|
| **AST/Parser** | **Python reimplementation** | Parsing is I/O-bound; pure Python with PEG parser (e.g., `lark`) works well. |
| **Codegen** | **PyO3 FFI** (or reimpl) | Codegen maps AST → bytecode instructions. Small enough to reimplement in Python using `flux_bridge.bytecode` module. |
| **Optimizer** | **PyO3 FFI** (performance critical) | Constant folding and DCE are simple pattern matches; Python is fast enough. |
| **Integration** | **Subprocess** | `load_and_run()` requires the Rust VM — subprocess to compiled binary. |
| **Fallback** | Pure Python codegen + Python bytecode VM | Slower chain (parser→codegen→Python VM) but works without Rust. |

### 5.4 cuda-oxide — PYTHON CANNOT REPLACE

| Aspect | Strategy | Details |
|--------|----------|---------|
| **Compilation** | **Subprocess** or **Rust FFI server** | 124K LOC LLVM pipeline — impossible to reimplement. Must call compiled Rust binary. |
| **PTX management** | **Subprocess** | Store/load PTX blobs as files. Call `cuda-oxide` binary with bytecode input. |
| **flux-importer** | **Subprocess** | Flux bytecode → MIR is tightly coupled to cuda-oxide internals. Subprocess only. |
| **Fallback** | **Not feasible** | No pure-Python PTX compiler exists. Must have Rust binary available or skip GPU compilation. |

### 5.5 cudaclaw — SYSTEM-LEVEL ONLY

| Aspect | Strategy | Details |
|--------|----------|---------|
| **CudaClawExecutor** | **Subprocess** | Launch persistent kernel via CLI. Communicate via pipe or shared memory. |
| **Command queue** | **Python construct** (via subprocess) | Use Python `ctypes` or `struct` to build `Command` (48 bytes) and write to queue shared with subprocess. |
| **LockFreeQueue** | **Python reimplementation** (via ctypes) | Spin on head/tail in shared memory. Atomic CAS available in Python via `mmap` + `ctypes` on Linux. |
| **PTX loading** | **Subprocess** | Pass PTX file path to binary as argument. |
| **GPU metrics** | **Subprocess** (nvidia-smi) | Python can call `nvidia-smi` directly — standard approach. |
| **Fallback** | **CPU-only** | All cudaclaw features require CUDA GPU. Fallback = `NotImplementedError` or queue commands to CPU executor. |

**Recommended architecture:**

```python
# flux_bridge/cudaclaw.py
class CudaClawConnection:
    """Subprocess connection to cudaclaw binary."""
    def __init__(self, ptx_path: str): ...
    def push_command(self, cmd_type: int, a: float = 0, b: float = 0) -> int: ...
    def poll_result(self, cmd_id: int) -> tuple[float, float] | None: ...
    def shutdown(self): ...
    def get_gpu_metrics(self) -> dict: ...  # via nvidia-smi subprocess
```

### 5.6 flux-autoscale — NATURAL FOR PYTHON

| Aspect | Strategy |
|--------|----------|
| **Entire crate** | **Python reimplementation** | Simple decision logic (threshold comparison, counter tracking). Zero performance bottleneck. |
| **Fallback** | Same (pure Python) | Already pure logic. |

### 5.7 ternary-svm — NATURAL FOR PYTHON (ML native)

| Aspect | Strategy |
|--------|----------|
| **Entire crate** | **Python reimplementation** (or use sklearn) | SVM is a standard ML algorithm. Use `scikit-learn` SVM with custom ternary kernels, or replicate the simplified SMO in Python. |
| **Ternary kernels** | **Python reimplementation** | Simple dot product + distance function. |
| **Fallback** | sklearn | `sklearn.svm.SVC` with precomputed kernel — more feature-rich than the Rust version. |

### 5.8 flux-vm-dispatch — PYTHON ASYNC NATIVE

| Aspect | Strategy |
|--------|----------|
| **Async executor** | **Python `asyncio` reimplementation** | Use `asyncio` directly. Rust's tokio has equivalent Python constructs. |
| **Batch execution** | **Python `asyncio.gather`** | Same pattern. |
| **Fallback** | Same (pure Python asyncio) | Already an async pattern. |

### Integration Summary Table

| Crate | Primary Strategy | Python Reimpl? | PyO3 FFI? | Subprocess? | Fallback |
|-------|-----------------|----------------|-----------|-------------|----------|
| **flux-core** | PyO3 FFI (hot path) | Vocabulary & A2A (safe) | Assembler, Disasm, Interpreter | Minimal | Python bytecode VM |
| **pincher-flux-bridge** | Python reimpl | ✅ Entire crate | None | None | Already Python |
| **plato-flux-compiler** | Mixed | Parser, Codegen (safe) | Optimizer | Integration (load/run) | Python VM |
| **flux-vm-dispatch** | Python reimpl | ✅ Entire pattern | None | None | asyncio |
| **ternary-svm** | Python reimpl | ✅ Entire crate | None | None | sklearn |
| **cuda-oxide** | Subprocess | ❌ Not feasible | Performance-critical | ✅ PTX compilation | Skip GPU path |
| **cudaclaw** | Subprocess + ctypes | Queue logic (ctypes) | None | ✅ GPU kernel mgmt | CPU-only fallback |
| **flux-autoscale** | Python reimpl | ✅ Entire crate | None | None | Already Python |

---

## 6. Test Coverage Analysis

### 6.1 flux-core — TESTS: Mixed Coverage

| Module | Tests | Location | Coverage |
|--------|-------|----------|----------|
| `vm/` (interpreter) | 7+ integration tests | `tests/test_vm.rs` | Core execution paths tested; edge cases (overflow, large programs) untested |
| `bytecode/` (assembler) | 5+ tests | `tests/test_assembler.rs` | Labels, branch offsets, comments tested; A2A format G not tested |
| `bytecode/` (disassembler) | Implicit in integration | Integration tests include round-trips | Direct unit tests missing |
| `a2a/` | 3+ tests | `tests/test_a2a.rs` | Basic serialize/deserialize; trust_score not tested; v1/v2 wire incompatibility not tested |
| `vocabulary/` | 8+ tests | `tests/vocabulary_tests.rs` | Regex matching, substitution, result extraction tested |
| `error.rs` | 0 dedicated tests | N/A | Error enum used implicitly in VM tests |
| `registers.rs` | 0 dedicated tests | N/A | Register bounds only tested indirectly via VM |
| `benches/vm_benchmark.rs` | 0 benchmarks | Standalone | Benchmarks file exists but may be stale |

**Missing test coverage:**
- Memory overflow edge cases (exactly 16MB, zero-size, negative allocation)
- A2A v1 vs v2 wire-format round-trips (the endianness difference is a bug waiting to happen)
- Stack overflow under deep recursion
- INOT/ISHL/ISHR edge cases (shift by large values)
- Concurrent A2A message ordering
- Format G instructions with non-trivial payloads

### 6.2 pincher-flux-bridge — TESTS: GOOD (9 tests)

| Module | Tests | Coverage |
|--------|-------|----------|
| `z3_add` | 1 test (5 assertions) | All 5 relevant cases tested |
| `z3_mul` | 1 test (4 assertions) | Good coverage |
| `reflex_to_flux` | 3 tests | Basic conversion, all-pass, threshold filtering, empty bundle |
| `flux_to_teach` | 2 tests | Round-trip, noise filtering |
| `ConversionFidelity` | 1 test | Perfect fidelity constructor |

**Missing:** No performance benchmarks, no stress test with >10K reflexes.

### 6.3 plato-flux-compiler — TESTS: GOOD (14 integration tests)

| Module | Tests | Coverage |
|--------|-------|----------|
| Integration tests | 14 tests in `integration_tests.rs` | HELLO_WORLD, FIBONACCI, LOOP, MATH, SUM_RECURSIVE, ERROR handling, empty programs, large offsets, negative offsets, end-to-end runs, optimizer test, load_and_run, flux_to_ptx |

**Missing:** No unit tests for parser or codegen individually; no optimizer edge cases (empty program, all-noop, etc.); no tests for unsupported AST nodes.

### 6.4 flux-vm-dispatch — No dedicated tests found

**Status:** Untested. Only has inline code examples in INSIGHT.md.

### 6.5 ternary-svm — IMPLICIT (few unit tests)

**Status:** lib.rs has no `#[cfg(test)] mod tests` block. The crate depends on `ternary-types` (git dep). SVM algorithm correctness depends on the SMO implementation being verified.

**Missing:** No unit tests for validate_ternary, kernel functions, SMO fitting, or prediction.

### 6.6 cudaclaw — TESTS: MODERATE

| Module | Tests | Coverage |
|--------|-------|----------|
| `cuda_claw.rs` | 7+ tests (feature-gated `#[cfg(feature = "cuda")]`) | Command serialization, QueueHost default, executor create/launch/stop/push, full queue |
| `dispatcher.rs` | Internal unit tests in file | SpinLock dispatch, LockFree push, batch, stop, stats, head advancement |
| `lock_free_queue.rs` | Internal tests | push_command, wait_for_space |
| `bridge.rs` | 5 tests | Creation, size, alignment, device pointer, allocate_command_queue, builder |
| `gpu_metrics.rs` | 0+ NVML tests | Metrics module exists but NVML-dependent tests not verified |

**Missing:**
- Kernel launch/stop with real GPU in CI
- Warp metric measurement accuracy
- Persistent worker stress test (>100K commands)
- Concurrent multi-thread push tests (they exist but are noted as potentially broken)
- All dispatchers with actual GPU execution (benchmark is demo code)

### 6.7 cuda-oxide — Tests exist inside llvm-export

| Module | Tests |
|--------|-------|
| `llvm-export` | Export test, ops test, common module |
| `fuzzer/rustlantis` | MIR fuzz testing infrastructure, difftests, config |

**Status:** Fuzzer infrastructure is comprehensive (difftest, MIR serialization, generation). The llvm-export crate has dedicated export and operations tests. However, the full Flux→MIR→PTX pipeline (flux-importer) is experimental and needs more end-to-end tests.

### 6.8 flux-autoscale — TESTS: GOOD (10 tests)

| Module | Tests | Coverage |
|--------|-------|----------|
| Autoscaler | 10 tests | Normal hold, backpressure scale-up, idle scale-down, max/min bounds, event tracking, multi-tick sequences, scaled-up detection |

**Missing:** No tests for custom thresholds, no concurrency tests, no tests with real stream metrics.

### Overall Coverage Summary

| Crate | Tests | Quality | Gaps |
|-------|-------|---------|------|
| flux-core | 23+ | Fair | No standalone register/memory tests, A2A wire format incompatibility |
| pincher-flux-bridge | 9 | Good | No stress tests |
| plato-flux-compiler | 14 | Good | No unit tests on parser/codegen |
| flux-vm-dispatch | 0 | **Missing** | Entirely untested |
| ternary-svm | 0 | **Missing** | Entirely untested |
| cudaclaw | 20+ | Fair | GPU-dependent tests can't run in CI |
| cuda-oxide | Various | Good (fuzzer) | Flux importer end-to-end untested |
| flux-autoscale | 10 | Good | No custom threshold tests |

---

## 7. Recommendations for the Bridge Implementation

### Recommendation #1 (CRITICAL): Use PyO3 for flux-core hot paths, pure Python for everything else

**Rationale:** The assembler, disassembler, and VM interpreter are performance-critical and complex. Reimplementing them in Python would be slow, bug-prone, and high maintenance. Everything else (pincher bridge, vocabulary, autoscaler, SVM) is straightforward data transformation or decision logic that Python handles beautifully.

**Implementation plan:**
```python
# tier-1 (PyO3 bindings in maturin):
flux_bridge/rust/  # maturin project
  └── flux_core_ffi/
      ├── assembler  → FluxAssembler
      ├── disassembler → FluxDisassembler
      └── interpreter → FluxVM
      ├── Cargo.toml (depends on flux-core git)
      └── src/lib.rs (PyO3 export functions)

# tier-2 (pure Python):
flux_bridge/pincher.py     # pincher-flux-bridge (reflex→FluxIR)
flux_bridge/vocabulary.py  # vocabulary system (regex + template)
flux_bridge/svm.py         # ternary-svm (or sklearn wrapper)
flux_bridge/autoscale.py   # flux-autoscale
```

### Recommendation #2 (CRITICAL): Subprocess for GPU-bound crates (cuda-oxide + cudaclaw)

**Rationale:** cuda-oxide depends on LLVM 19.x + CUDA SDK 12.4+. Wrapping this via PyO3 creates build dependency hell. cudaclaw requires CUDA runtime and GPU hardware. Both should be separate binaries that the bridge communicates with via subprocess (stdin/stdout JSON or protobuf).

**Architecture:**
```
Python Bridge (flux_bridge)
  → subprocess('cuda-oxide compile --input bytecode.bin --output kernel.ptx')
  → subprocess('cudaclaw launch --ptx kernel.ptx --streams 4')
  → reads stdout JSON for results/metadata
```

**Alternative for performance:** Run cuda-oxide as a gRPC server and cudaclaw as a persistent daemon — eliminates subprocess overhead for repeated invocations.

### Recommendation #3 (HIGH): Ship a pure-Python FLUX bytecode interpreter as fallback

**Rationale:** The bridge must work even when Rust isn't available (e.g., pip install on a machine without Rust toolchain). A minimal Python interpreter can execute basic FLUX bytecode (arithmetic, stack ops, memory) with no dependencies.

**Fallback tiers:**
```
Tier 1 (full performance):
  PyO3-backed FluxVM (assembler + interpreter in Rust)

Tier 2 (no Rust compilation available):
  Pure Python FluxVM (reimplements opcode execution in Python)
    - ~50x slower than Rust but works everywhere
    - Supports all 30 opcodes
    - No A2A (not performance-critical)
    - Interface-compatible with Tier 1

Tier 3 (no GPU):
  Everything still works for CPU execution
  cudaclaw operations raise NotImplementedError with clear message
```

### Recommendation #4 (HIGH): Implement A2A message encoding in Python for debug/development

**Rationale:** A2A message serialization is simple (fixed fields + byte payload) but has v1/v2 endianness differences. A Python implementation is the right place to:
1. Handle both v1 (LE) and v2 (BE) wire formats transparently
2. Provide a human-readable debug view of A2A messages
3. Generate test fixtures for validating Rust-side A2A parsing

```python
class A2AMessage:
    @staticmethod
    def serialize_v1(sender: UUID, receiver: UUID, conv_id: UUID,
                      msg_type: int, payload: bytes, trust: float) -> bytes: ...
    @staticmethod
    def serialize_v2(sender: UUID, receiver: UUID, conv_id: UUID,
                      msg_type: int, payload: bytes, trust: float) -> bytes: ...
```

### Recommendation #5 (MEDIUM): Create a unified CLI that wraps the entire stack

**Rationale:** The most common use case for the Python bridge is: "I have an intent / assembly text → I want GPU execution." A single CLI entry point would dramatically improve developer experience.

```python
# flux_bridge/cli.py — main entry point
def run_pipeline(intent_or_assembly: str, *, 
                 target: Literal["cpu", "ptx"] = "cpu",
                 confidence_threshold: float = 0.5,
                 max_gpu_streams: int = 4) -> PipelineResult:
    """
    1. If input is assembly text → assemble to bytecode
    2. If input is intent → use pincher bridge (reflex_to_flux + assemble)
    3. Optionally compile to PTX via cuda-oxide subprocess
    4. Execute on CPU (flux-core VM) or GPU (cudaclaw subprocess)
    5. Return result + latency metrics
    """
    ...
```

**Usage from Python:**
```python
from flux_bridge import run_pipeline

# CPU execution (always works)
result = run_pipeline("MOVI R0, 42\nMOVI R1, 10\nIADD R0, R0, R1\nHALT")
print(result.registers["R0"])  # 52

# GPU execution (requires Rust + CUDA)
result = run_pipeline("vec_add bytecode...", target="ptx", max_gpu_streams=2)
print(result.gpu_metrics)
```

**Additional recommendations (lower priority):**

- **#6:** Wrap `cuda-oxide` compilation in a persistent daemon process for repeated compilation requests (avoids LLVM startup overhead)
- **#7:** Expose `ConversionFidelity` metrics from the pincher bridge — users should know how many reflexes were skipped
- **#8:** Implement the `Vocabulary` system in Python first (more flexible than Rust regex) and use it as the bridge's default intent handler
- **#9:** Add a `flux_bridge validate <bytecode>` command that runs the disassembler and verifies bytecode structure
- **#10:** Create a `flux_bridge.svm.TernarySVM` class that wraps sklearn SVMs with ternary kernel preprocessing — more robust than the Rust simplified SMO

---

## Appendix: Quick Reference

### Compilation Pipeline Commands (proposed CLI)

```bash
# Assemble FLUX text to bytecode
flux-bridge assemble program.flux -o program.bc

# Disassemble bytecode to text
flux-bridge disassemble program.bc

# Execute on CPU VM
flux-bridge run program.bc

# Compile bytecode to PTX (requires cuda-oxide binary)
flux-bridge compile program.bc -o kernel.ptx --target sm_86

# Launch GPU execution (requires cudaclaw binary + GPU)
flux-bridge gpu kernel.ptx --streams 4

# Full pipeline: intent → GPU
flux-bridge pipeline "compute 5 + 3" --target gpu --confidence 0.7

# Autoscale GPU streams
flux-bridge autoscale --min 1 --max 16 --metrics gpu_metrics.json

# Train ternary SVM
flux-bridge svm train --data features.csv --kernel rbf --c 1.0

# Monitor GPU metrics
flux-bridge gpu-monitor --interval 100ms
```

### Key Constants Reference

| Constant | Value | Context |
|----------|-------|---------|
| `QUEUE_SIZE` | 1024 | cudaclaw CommandQueue capacity |
| `COMMAND_SIZE` | 48 bytes | cudaclaw Command struct |
| `QUEUE_TOTAL_SIZE` | 49,192 bytes | CommandQueueHost struct size |
| `MAX_GP_REGISTERS` | 16 | flux-core integer registers |
| `MAX_FP_REGISTERS` | 16 | flux-core float registers |
| `MAX_SIMD_REGISTERS` | 16 | flux-core SIMD registers |
| `DEFAULT_MEMORY` | 64 KB | flux-core VM memory size |
| `MAX_MEMORY` | 16 MB | flux-core VM max memory |
| `PAGE_SIZE` | 4 KB | flux-core VM page size |
| `CYCLE_LIMIT` | 10,000,000 | flux-core default instruction budget |
| `MAX_PAYLOAD` | 65,535 bytes | A2A message max payload (u16) |
| `TRUST_RANGE` | 0.0 - 1.0 | A2A trust score range |
| `DEFAULT_TRUST` | 1.0 | A2A default trust score |
| `A2A_V1_HEADER` | 55 bytes | Without timestamp field |
| `A2A_V2_HEADER` | 63 bytes | With timestamp field |

### Repository Relationship Summary

```
                               PLATO
                           (AST → bytecode)
                                 │
    PINCHER-FLUX-BRIDGE          │          FLUX-VM-DISPATCH
    (reflexes → FluxIR)          │          (async VM scheduling)
               │                 │                 │
               └──────────┬──────┘                 │
                          ▼                        │
                     FLUX-CORE                     │
                (VM + A2A + vocabulary)             │
                          │                        │
                          ▼                        │
                    CUDA-OXIDE                     │
                (Flux bytecode → PTX)              │
                          │                        │
                          ▼                        │
                     CUDACLAW                      │
                (GPU persistent kernel)            │
                          │                        │
                          ▼                        │
                FLUX-AUTOSCALE ◄───────────────────┘
                (stream scaling)

    TERNARY-SVM (standalone ML for ternary feature spaces)
```

---
*End of ECOSYSTEM-SYNTHESIS.md — 8 repos analyzed, 50+ source files reviewed.*
