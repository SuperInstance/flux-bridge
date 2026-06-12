# Fleet Integration Plan — flux_bridge × 16 Fleet-MIDI Agents

## 1. Current Architecture

```
HTTP Request → fleet-agent.py → 16 subprocesses (ports 2160-2175)
                                   ├── chord (2160)
                                   ├── scale (2161)
                                   ├── melody (2174)
                                   └── bass (2175)
```

Each agent:
- Runs as a standalone process spawned by `fleet-agent.py`
- Listens on its assigned port via HTTP
- Processes cue/think requests from the Fleet Conductor (port 8769)
- Has **no inter-agent communication** — each is an island
- Character state is **in-memory only** — lost on restart

## 2. Target Architecture (FLUX-native)

```
LLM Agent → FLUX Bytecode → SwarmRouter → 16 A2A Agent Mailboxes
                                            ├── chord (Ask/Tell)
                                            ├── scale (Delegate)
                                            ├── melody (Broadcast)
                                            └── bass (Tell)
                                            │
                              CharacterStore (fleet_characters/db/)
                              ├── Per-agent stats, dreams, class
                              └── SQLite persistence
```

Each agent:
- Receives A2AMessages (Tell/Ask/Delegate/Broadcast) instead of HTTP JSON
- Runs FLUX bytecode programs on the PythonInterpreter
- Communicates with other agents via A2A Signal protocol
- Character state persisted to SQLite via `fleet_characters/db/`

## 3. Phased Migration

### Phase A: Hybrid (Week 1)
- Agents speak both HTTP + A2A concurrently
- SwarmRouter sits alongside the HTTP server
- All inbound requests are mirrored to A2A messages
- Character stats grow from both HTTP and A2A activity
- **Risk**: None — additive change, no breakage

### Phase B: Native (Week 2)
- Agents run bytecode programs for cue/think processing
- PythonInterpreter replaces the HTTP handler for computation
- A2A messages drive inter-agent coordination
- HTTP endpoints become thin wrappers around A2A messages
- **Risk**: Bytecode programs must match existing logic exactly

### Phase C: Pure (Week 3)
- All inter-agent communication through A2A Signal protocol
- HTTP endpoints removed from agent processes
- Fleet Conductor sends A2A messages instead of HTTP POST
- Character persistence to SQLite is automatic
- **Risk**: Fleet Conductor must be updated to speak A2A

## 4. API Surface Migration

| Current (HTTP) | Target (A2A) | Mapping |
|----------------|-------------|---------|
| `POST /agent {type:cue}` | `A2AMessage(Tell)` | cue → Tell with bytecode payload |
| `POST /agent {type:think}` | `A2AMessage(Ask)` | think → Ask with intent payload |
| `GET /status` | `VMResult.registers` | VM register dump |
| `POST /teach` | `A2AMessage(Delegate)` | teach → Delegate with FluxIR |
| — (none) | `A2AMessage(Broadcast)` | Broadcast to all agents |
| — (none) | `CharacterStore.save()` | Auto-persist on state change |

## 5. Port Migration (ports stay, protocol changes)

| Port | Agent | Phase A | Phase B | Phase C |
|------|-------|---------|---------|---------|
| 2160 | chord | HTTP + A2A | A2A + bytecode | Pure A2A |
| 2161 | scale | HTTP + A2A | A2A + bytecode | Pure A2A |
| ... | ... | ... | ... | ... |
| 2175 | bass | HTTP + A2A | A2A + bytecode | Pure A2A |

## 6. Integration Points

### Character Store Integration
```python
from fleet_characters.db import open, CharacterStore, create_schema

db = open("fleet-characters.db")
store = CharacterStore(db)
create_schema(db)

# On agent startup, load existing character
char_data = store.load("ChordMaster", "chord")
if char_data:
    agent = assemble_agent_from_dict(char_data)
else:
    agent = AgentCharacter("ChordMaster", "chord")
```

### SwarmRouter + Character Stats
```python
router = SwarmRouter()

def on_message(msg: A2AMessage, agent_char: AgentCharacter):
    # Process message
    result = PythonInterpreter().execute(msg.payload)
    # Update character
    agent_char.process_request('cue', success=result.success)
    # Persist
    store.save(agent_char)
    # Route response
    router.route(response_msg)
```

## 7. A2A Message Flow for Common Operations

### Cue Analysis
```
User → SwarmRouter → chord-agent (Ask)
  chord-agent executes bytecode
  chord-agent → SwarmRouter → User (Tell with analysis result)
  chord-agent character stats update
  CharacterStore.save(chord-agent)
```

### Multi-Agent Harmony Analysis  
```
User → SwarmRouter → chord-agent (Ask)
  chord-agent → melody-agent (Ask for harmonic context)
    melody-agent → scale-agent (Ask for scale reference)
    melody-agent ← scale-agent (Tell with scale)
  chord-agent ← melody-agent (Tell with context)
  chord-agent computes final analysis
User ← chord-agent (Tell with final result)
All agents: CharacterStore.save()
```

## 8. Rollback Strategy

If anything breaks during migration:
1. **Phase A**: Stop A2A listener, revert to pure HTTP. Zero data loss (CharacterStore persists independently)
2. **Phase B**: Keep HTTP handlers as fallback. Disable bytecode execution, use Python handlers
3. **Phase C**: Phase B fallback available by restarting agent with `--legacy-http` flag

## 9. Success Metrics

| Metric | Current | Target | Measurement |
|--------|---------|--------|-------------|
| Latency per request | ~5-15ms | ~0.5-2ms | `time curl` |
| Inter-agent communication | None | Full A2A | Messages/sec |
| State persistence | In-memory | SQLite | Characters loaded after restart |
| Agent throughput | ~100 req/s | ~1000 req/s | Requests per second |
| Cross-agent coordination | Manual | Automatic | Successful multi-agent flows |

## 10. Immediate Next Steps

1. ✅ flux_bridge shipped (bytecode, VM, A2A, prompts, tests)
2. ✅ Interoperability PoC running (3 agents, 4ms execution)
3. 🔲 Build Phase A hybrid adapter for fleet-agent.py
4. 🔲 Convert Fleet Conductor to emit A2A messages
5. 🔲 Run character persistence on real agent ports
6. 🔲 Benchmark Phase B vs Phase A latency
