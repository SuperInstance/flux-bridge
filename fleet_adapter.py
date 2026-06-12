#!/usr/bin/env python3
"""
fleet_adapter — Phase A hybrid HTTP+A2A adapter for the 16 fleet-midi agents.

Each agent (chord=2160 through bass=2175) gets an A2A mailbox alongside
its existing HTTP endpoint. Inbound requests are mirrored to A2A messages.
Character stats persist to SQLite via fleet_characters/db/.
"""

import os
import sys
import json
import time
import http.server
import threading
import subprocess
from typing import Optional, Dict, Any, List

# ── Path setup ─────────────────────────────────────────────────────
WORKSPACE = os.path.expanduser('~/.openclaw/workspace')
for p in [WORKSPACE, os.path.join(WORKSPACE, 'fleet-characters')]:
    if p not in sys.path:
        sys.path.insert(0, p)

from fleet_characters import AgentCharacter
from fleet_characters.db import open as db_open, CharacterStore, create_schema, close as db_close, Cap
from flux_bridge.signal_router import A2AMessage, MessageType, SwarmRouter
from flux_bridge.vm_harness import PythonInterpreter


# ── Port map ───────────────────────────────────────────────────────
AGENT_PORT_MAP = {
    'chord': 2160, 'scale': 2161, 'voicing': 2162, 'tempo': 2163,
    'cc': 2164, 'expression': 2165, 'dynamics': 2166, 'pan': 2167,
    'modulation': 2168, 'arp': 2169, 'groove': 2170, 'velocity': 2171,
    'fx': 2172, 'register': 2173, 'melody': 2174, 'bass': 2175,
}
PORT_AGENT_MAP = {v: k for k, v in AGENT_PORT_MAP.items()}
AGENT_NAMES = {
    'chord': 'ChordMaster', 'scale': 'ScaleSage', 'voicing': 'VoiceCrafter',
    'tempo': 'TempoWeaver', 'cc': 'CCWarden', 'expression': 'Expresso',
    'dynamics': 'DynaFlex', 'pan': 'PanOpticon', 'modulation': 'ModMatrix',
    'arp': 'ArpEngine', 'groove': 'GrooveMech', 'velocity': 'Velociraptor',
    'fx': 'FxAlchemist', 'register': 'Registrar', 'melody': 'MelodySage',
    'bass': 'BassLine',
}


class HybridAgent:
    """One fleet-midi agent with both HTTP and A2A capabilities."""

    def __init__(self, domain: str, store: CharacterStore):
        self.domain = domain
        self.port = AGENT_PORT_MAP[domain]
        self.agent_name = AGENT_NAMES[domain]
        self.character: AgentCharacter = None
        self.store = store
        self.mailbox: List[A2AMessage] = []
        self.mailbox_lock = threading.Lock()
        self.last_save_at = time.time()
        self._vm = PythonInterpreter()

        # Try to load existing character
        char_data = store.load(self.agent_name, self.domain)
        if char_data:
            from fleet_characters.agent_profile import assemble_agent_from_dict
            self.character = assemble_agent_from_dict(char_data)
        else:
            self.character = AgentCharacter(self.agent_name, self.domain)
            store.save(self.character)

    def process_request(self, request_type: str = "cue", success: bool = True,
                        response_time_ms: float = 100.0) -> Dict[str, Any]:
        """Process a request (unchanged from fleet-agent behavior)."""
        result = self.character.process_request(request_type, success, response_time_ms)
        self._maybe_save()
        return result

    def process_a2a(self, msg: A2AMessage) -> Optional[Dict[str, Any]]:
        """Process an A2A message. Returns response if Ask, None if Tell."""
        if msg.message_type == MessageType.Tell:
            self.character.process_request('cue', success=True, response_time_ms=10)
            self._maybe_save()
            return None

        elif msg.message_type == MessageType.Ask:
            # Execute payload as bytecode if possible
            try:
                result = self._vm.execute(msg.payload)
                self.character.process_request('think', success=result.success,
                                                response_time_ms=result.cycles_used)
                self._maybe_save()
                return {
                    'success': result.success,
                    'registers': result.registers[:8],
                    'cycles': result.cycles_used,
                    'halted': result.halted,
                }
            except Exception:
                self.character.process_request('think', success=False, response_time_ms=100)
                self._maybe_save()
                return {'success': False}

        elif msg.message_type == MessageType.Delegate:
            # Delegate = teach a new reflex
            try:
                self.character.process_request('think', success=True, response_time_ms=20)
                self._maybe_save()
                return {'success': True}
            except Exception:
                return {'success': False}

        return None

    def deliver(self, msg: A2AMessage):
        """Deliver a message to this agent's mailbox."""
        with self.mailbox_lock:
            self.mailbox.append(msg)

    def drain_mailbox(self) -> List[A2AMessage]:
        """Drain all pending messages."""
        with self.mailbox_lock:
            msgs = list(self.mailbox)
            self.mailbox.clear()
        return msgs

    def trigger_dream(self) -> Dict[str, Any]:
        """Trigger a dream cycle if we have enough experiences."""
        if self.character.total_requests >= 100 and self.character.tick % 100 < 10:
            report = self.character.run_dream_cycle()
            self._maybe_save(force=True)
            return report
        return {}

    def to_dict(self) -> Dict[str, Any]:
        return self.character.to_dict()

    def _maybe_save(self, force: bool = False):
        """Auto-save every 30 seconds or 10 requests."""
        now = time.time()
        if force or now - self.last_save_at > 30 or self.character.tick % 10 == 0:
            self.store.save(self.character)
            self.last_save_at = now

    def close(self):
        """Save and clean up."""
        self.store.save(self.character)


class FleetAdapter:
    """Manages all 16 hybrid agents."""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(WORKSPACE, '..', '.openclaw', 'fleet-characters.db')
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

        self.db = db_open(db_path, Cap.FS_WRITE)
        create_schema(self.db)
        self.store = CharacterStore(self.db)
        self.router = SwarmRouter()
        self.agents: Dict[str, HybridAgent] = {}
        self._running = True

        # Initialize all 16 agents
        for domain in AGENT_PORT_MAP:
            agent = HybridAgent(domain, self.store)
            self.agents[domain] = agent
            agent_id = f'{domain}-1'
            self.router.register_agent(agent_id, agent.agent_name, domain, character=agent.character)

    def get_agent(self, domain: str) -> Optional[HybridAgent]:
        return self.agents.get(domain)

    def get_agent_by_port(self, port: int) -> Optional[HybridAgent]:
        domain = PORT_AGENT_MAP.get(port)
        return self.agents.get(domain) if domain else None

    def status(self) -> Dict[str, Any]:
        """Get status of all agents."""
        return {
            domain: agent.to_dict()
            for domain, agent in self.agents.items()
        }

    def broadcast(self, payload: bytes):
        """Broadcast a message to all agents."""
        msg = A2AMessage(
            sender=b'fleet-adapter'.ljust(16, b'\x00'),
            receiver=b'\x00' * 16,
            conversation_id=os.urandom(16),
            message_type=MessageType.Broadcast,
            payload=payload,
            trust_score=0.9,
        )
        for agent in self.agents.values():
            agent.deliver(msg)

    def dream_cycle_all(self):
        """Trigger dream cycles on all qualified agents."""
        results = {}
        for domain, agent in self.agents.items():
            report = agent.trigger_dream()
            if report:
                results[domain] = report
        return results

    def save_all(self):
        """Save all agents to database."""
        for agent in self.agents.values():
            agent.store.save(agent.character)

    def close(self):
        """Save all agents and close database."""
        self.save_all()
        db_close(self.db)
        self._running = False


# ── HTTP Server (mirrors fleet-agent.py behavior) ──────────────────

class FleetAdapterHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler that bridges to the FleetAdapter's A2A layer."""

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length) if length else b'{}'
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        adapter: FleetAdapter = self.server.adapter
        domain = data.get('domain', 'chord')
        agent = adapter.get_agent(domain)

        if self.path == '/agent':
            req_type = data.get('type', 'cue')
            success = data.get('success', True)
            result = agent.process_request(req_type, success, data.get('response_time_ms', 100))
            self._respond(200, result)

        elif self.path == '/a2a':
            msg_type = data.get('message_type', 'Tell')
            payload_str = data.get('payload', '')
            payload = payload_str.encode() if isinstance(payload_str, str) else payload_str
            mt_map = {'Tell': MessageType.Tell, 'Ask': MessageType.Ask,
                      'Delegate': MessageType.Delegate, 'Broadcast': MessageType.Broadcast}
            msg = A2AMessage(
                sender=f"{domain}-1".ljust(16, '\x00').encode()[:16],
                receiver=data.get('receiver', domain).ljust(16, '\x00').encode()[:16],
                conversation_id=os.urandom(16),
                message_type=mt_map.get(msg_type, MessageType.Tell),
                payload=bytes(payload),
                trust_score=data.get('trust_score', 0.5),
            )
            result = agent.process_a2a(msg)
            self._respond(200, result or {'ok': True})

        elif self.path == '/teach':
            intent = data.get('intent', '')
            from flux_bridge.bytecode import Assembler, validate
            a = Assembler()
            # Simple intent→bytecode mapping
            if 'add' in intent:
                parts = intent.replace('add', '').strip().split()
                if len(parts) >= 2:
                    a.emit_mov_i(0, int(parts[0]))
                    a.emit_mov_i(1, int(parts[1]))
                    a.emit_add(0, 1)
            a.emit_halt()
            bc = a.assemble()
            vr = validate(bc)
            self._respond(200, {
                'intent': intent,
                'bytecode_hex': a.assemble_to_hex(),
                'valid': vr.safe,
                'warnings': vr.warnings,
                'length': len(bc),
            })

        else:
            self._respond(404, {'error': 'not found'})

    def do_GET(self):
        adapter: FleetAdapter = self.server.adapter
        if self.path == '/status':
            self._respond(200, adapter.status())
        elif self.path == '/health':
            self._respond(200, {'status': 'ok', 'agents': len(adapter.agents)})
        else:
            self._respond(404, {'error': 'not found'})

    def _respond(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # Suppress HTTP log spam


def create_http_adapter(host='localhost', port=2176, db_path=None) -> http.server.HTTPServer:
    """Create an HTTP server wrapping the FleetAdapter."""
    adapter = FleetAdapter(db_path)
    server = http.server.HTTPServer((host, port), FleetAdapterHandler)
    server.adapter = adapter
    return server


# ── CLI entry point ────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Fleet Adapter — hybrid HTTP+A2A')
    parser.add_argument('--port', type=int, default=2176, help='HTTP server port')
    parser.add_argument('--host', default='localhost', help='HTTP server host')
    parser.add_argument('--db', default=None, help='SQLite database path')
    parser.add_argument('--status', action='store_true', help='Print status and exit')
    args = parser.parse_args()

    if args.status:
        adapter = FleetAdapter(args.db)
        print(json.dumps(adapter.status(), indent=2))
        adapter.close()
        return

    server = create_http_adapter(args.host, args.port, args.db)
    print(f'Fleet Adapter listening on {args.host}:{args.port}')
    print(f'  Agents: {len(adapter.agents)} ({", ".join(AGENT_PORT_MAP.keys())})')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...')
        server.adapter.save_all()
        server.shutdown()


if __name__ == '__main__':
    main()
