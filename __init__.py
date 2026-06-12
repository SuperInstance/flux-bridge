"""
flux_bridge — Python bridge to the FLUX ecosystem.

LLM Translation Engine + VM Harness + A2A Signal Router + Constraint Validator.

Architecture:
    LLM Agent → LLM Engine → Bytecode → VM Harness → A2A Signal Router → Fleet Agents

Usage:
    from flux_bridge import (
        # Bytecode utilities
        Op, Assembler, Disassembler, validate,

        # VM
        VMHarness, VMResult, PythonInterpreter,

        # A2A / Signal
        A2AMessage, MessageType, SwarmRouter, AgentRegistry,

        # LLM Engine
        LLMTranslationEngine,
    )
"""

# ─── Version ──────────────────────────────────────────────────────────
__version__ = "0.1.0"
__flux_compat__ = "flux-core v0.1.0 (29 tests passing)"
__arm64_verified__ = True

# ─── Lazy imports — submodules are populated by build_subagents ────────
# Each submodule is imported on first access. If a module hasn't been
# built yet (subagent still running), a clear ImportError with instructions
# is raised.

import importlib
import sys as _sys


def __getattr__(name: str):
    """Lazy-load submodules when accessed."""
    module_map = {
        # Bytecode utilities (subagent flux-bytecode)
        "Op": "flux_bridge.bytecode.opcodes",
        "Assembler": "flux_bridge.bytecode.assembler",
        "Disassembler": "flux_bridge.bytecode.disassembler",
        "validate": "flux_bridge.bytecode.validator",

        # VM Harness (subagent flux-vm-harness)
        "VMHarness": "flux_bridge.vm_harness",
        "VMResult": "flux_bridge.vm_harness",
        "PythonInterpreter": "flux_bridge.vm_harness",

        # A2A Signal Router (subagent flux-signal-router)
        "A2AMessage": "flux_bridge.signal_router",
        "MessageType": "flux_bridge.signal_router",
        "SwarmRouter": "flux_bridge.signal_router",
        "AgentRegistry": "flux_bridge.signal_router",

        # LLM Engine (subagent flux-llm-engine)
        "LLMTranslationEngine": "flux_bridge.llm_engine",

        # Schemas
        "FluxConstraint": "flux_bridge.schemas.constraints",
        "ConstraintSet": "flux_bridge.schemas.constraints",
    }

    if name in module_map:
        module_path = module_map[name]
        try:
            mod = importlib.import_module(module_path)
            return getattr(mod, name)
        except ImportError as e:
            raise ImportError(
                f"flux_bridge.{name} not available yet. "
                f"Expected in {module_path}. "
                f"Subagent may still be running: {e}"
            ) from e

    standard = {"ECOSYSTEM-SYNTHESIS", "readme"}
    if name in standard:
        raise AttributeError(
            f"flux_bridge.{name} is a document, not a module. "
            f"Read the file at flux_bridge/{name.lower().replace('-', '/')}.md"
        )

    raise AttributeError(f"module 'flux_bridge' has no attribute '{name}'")


# ─── Top-level API help ─────────────────────────────────────────────

def health_check() -> dict:
    """Check if all submodules are available and working."""
    results = {}
    checks = [
        ("bytecode.opcodes", "from flux_bridge.bytecode.opcodes import Op"),
        ("bytecode.assembler", "from flux_bridge.bytecode.assembler import Assembler"),
        ("bytecode.disassembler", "from flux_bridge.bytecode.disassembler import Disassembler"),
        ("bytecode.validator", "from flux_bridge.bytecode.validator import validate"),
        ("vm_harness", "from flux_bridge.vm_harness import VMHarness, PythonInterpreter"),
        ("signal_router", "from flux_bridge.signal_router import A2AMessage, SwarmRouter"),
        ("llm_engine", "from flux_bridge.llm_engine import LLMTranslationEngine"),
        ("schemas.constraints", "from flux_bridge.schemas.constraints import FluxConstraint"),
    ]
    for name, stmt in checks:
        try:
            exec(stmt)
            results[name] = "✅"
        except ImportError as e:
            results[name] = f"❌ {e}"
        except Exception as e:
            results[name] = f"⚠️ {e}"
    results["_all_ready"] = all(v == "✅" for v in results.values())
    results["_version"] = __version__
    return results


def status_report() -> str:
    """Human-readable status of the flux_bridge module."""
    hc = health_check()
    lines = [
        "╔══════════════════════════════════════════╗",
        "║   flux_bridge Status Report              ║",
        f"║   v{__version__} | ARM64: {'✅' if __arm64_verified__ else '⚠️'}        ║",
        "╠══════════════════════════════════════════╣",
    ]
    for name, status in sorted(hc.items()):
        if name.startswith("_"):
            continue
        icon = "✅" if status == "✅" else "❌"
        lines.append(f"  {icon} {name}")
    lines.append(f"  {'✅' if hc.get('_all_ready') else '⚠️'} All modules ready: {hc.get('_all_ready', False)}")
    lines.append("╚══════════════════════════════════════════╝")
    return "\n".join(lines)
