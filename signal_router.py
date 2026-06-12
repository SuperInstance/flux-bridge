"""A2A Signal Router — binary-compatible message routing for flux_bridge.

Wire format (matching Rust A2AMessage in flux-core/src/a2a/messages.rs):
    [0..16]   sender UUID (16 bytes)
    [16..32]  receiver UUID (16 bytes)
    [32..48]  conversation_id UUID (16 bytes)
    [48]      message_type (1 byte: 1=Tell, 2=Ask, 3=Delegate, 4=Broadcast)
    [49..51]  payload length (u16 little-endian)
    [51..51+plen] payload (variable, max 65535 bytes)
    [51+plen..55+plen] trust_score (f32 little-endian, 4 bytes)

Minimum message size: 55 bytes (empty payload).
"""

from __future__ import annotations

import json
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────
# MessageType — mirrors Rust enum in flux-core/src/a2a/messages.rs
# ──────────────────────────────────────────────────────────────────────

class MessageType(IntEnum):
    """A2A message types, matching the Rust MessageType enum values."""
    Tell = 1
    Ask = 2
    Delegate = 3
    Broadcast = 4


# ──────────────────────────────────────────────────────────────────────
# A2AMessage — binary-compatible with Rust A2AMessage
# ──────────────────────────────────────────────────────────────────────

@dataclass
class A2AMessage:
    """Binary-compatible A2A message, matching the Rust struct exactly.

    Fields:
        sender:            16-byte UUID identifying the source agent.
        receiver:          16-byte UUID identifying the target agent;
                           zeroed for Broadcast.
        conversation_id:   16-byte UUID tying messages into a conversation chain.
        message_type:      Tell | Ask | Delegate | Broadcast.
        payload:           Raw bytes payload (max 65535 bytes).
        trust_score:       Float32 trust value stored as 4 LE bytes.
    """
    sender: bytes
    receiver: bytes
    conversation_id: bytes
    message_type: MessageType
    payload: bytes
    trust_score: float = 0.5

    def __post_init__(self):
        """Validate field sizes on construction."""
        if len(self.sender) != 16:
            raise ValueError(f"sender must be 16 bytes, got {len(self.sender)}")
        if len(self.receiver) != 16:
            raise ValueError(f"receiver must be 16 bytes, got {len(self.receiver)}")
        if len(self.conversation_id) != 16:
            raise ValueError(f"conversation_id must be 16 bytes, got {len(self.conversation_id)}")
        if not isinstance(self.message_type, MessageType):
            self.message_type = MessageType(self.message_type)
        if len(self.payload) > 65535:
            raise ValueError(f"payload exceeds 65535 bytes ({len(self.payload)})")

    # ── Constructors ──────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        sender: bytes,
        receiver: bytes,
        msg_type: MessageType,
        payload: bytes,
        conversation_id: Optional[bytes] = None,
        trust_score: float = 0.5,
    ) -> A2AMessage:
        """Create a new message with optional conversation_id.

        If conversation_id is None a fresh random UUID is generated.
        """
        if conversation_id is None:
            conversation_id = uuid.uuid4().bytes
        return cls(
            sender=sender,
            receiver=receiver,
            conversation_id=conversation_id,
            message_type=msg_type,
            payload=payload,
            trust_score=trust_score,
        )

    # ── Binary serialisation (Rust-compatible) ────────────────────

    def to_bytes(self) -> bytes:
        """Serialise to the exact binary format matching Rust A2AMessage.

        Layout:
            sender (16) + receiver (16) + conversation_id (16)
            + msg_type u8 (1) + payload_len u16 LE (2)
            + payload (variable) + trust_score f32 LE (4)
        """
        payload_len = len(self.payload)
        if payload_len > 65535:
            raise ValueError(f"payload too large: {payload_len} > 65535")

        buf = bytearray()
        buf.extend(self.sender)                               # [0..16]
        buf.extend(self.receiver)                             # [16..32]
        buf.extend(self.conversation_id)                      # [32..48]
        buf.append(self.message_type.value)                   # [48]
        buf.extend(struct.pack('<H', payload_len))            # [49..51]
        buf.extend(self.payload)                              # [51..]
        buf.extend(struct.pack('<f', self.trust_score))       # last 4 bytes
        return bytes(buf)

    @classmethod
    def from_bytes(cls, data: bytes) -> Optional[A2AMessage]:
        """Deserialise from the exact binary format.

        Returns None on any parsing failure (wrong length, unknown type, etc.).
        Mirrors Rust A2AMessage::from_bytes().
        """
        # Minimum: 16+16+16+1+2+0+4 = 55 bytes
        if len(data) < 55:
            return None

        sender = data[0:16]
        receiver = data[16:32]
        conversation_id = data[32:48]

        try:
            msg_type = MessageType(data[48])
        except ValueError:
            return None

        payload_len = struct.unpack_from('<H', data, 49)[0]
        payload_end = 51 + payload_len
        if len(data) < payload_end + 4:
            return None

        payload = data[51:payload_end]
        trust_score = struct.unpack_from('<f', data, payload_end)[0]

        return cls(
            sender=sender,
            receiver=receiver,
            conversation_id=conversation_id,
            message_type=msg_type,
            payload=payload,
            trust_score=trust_score,
        )

    # ── JSON interchange (for fleet HTTP agents) ──────────────────

    def to_json(self) -> str:
        """Serialise to JSON using hex-encoded UUIDs and base64 payload."""
        return json.dumps({
            "sender": self.sender.hex(),
            "receiver": self.receiver.hex(),
            "conversation_id": self.conversation_id.hex(),
            "message_type": self.message_type.value,
            "message_type_name": self.message_type.name,
            "payload": self.payload.hex(),
            "trust_score": round(self.trust_score, 6),
        })

    @classmethod
    def from_json(cls, text: str) -> A2AMessage:
        """Deserialise from JSON (hex-encoded fields)."""
        data = json.loads(text)
        return cls(
            sender=bytes.fromhex(data["sender"]),
            receiver=bytes.fromhex(data["receiver"]),
            conversation_id=bytes.fromhex(data.get("conversation_id", "0" * 32)),
            message_type=MessageType(data["message_type"]),
            payload=bytes.fromhex(data["payload"]),
            trust_score=float(data.get("trust_score", 0.5)),
        )

    # ── Helpers ───────────────────────────────────────────────────

    def short(self) -> str:
        """Short human-readable representation."""
        return (
            f"A2A::{self.message_type.name}(conv={self.conversation_id.hex()[:8]}, "
            f"trust={self.trust_score:.2f}, {len(self.payload)}B)"
        )

    def __repr__(self) -> str:
        return (
            f"A2AMessage(sender={self.sender.hex()[:8]}…, "
            f"receiver={self.receiver.hex()[:8]}…, "
            f"conv={self.conversation_id.hex()[:8]}…, "
            f"type={self.message_type.name}, "
            f"trust={self.trust_score:.2f}, "
            f"payload={len(self.payload)}B)"
        )


# ──────────────────────────────────────────────────────────────────────
# AgentRegistry — registration, lookup, and mailbox delivery
# ──────────────────────────────────────────────────────────────────────

@dataclass
class AgentInfo:
    """Information about a registered agent."""
    agent_id: bytes          # 16-byte UUID
    name: str
    domain: str
    inbox: List[A2AMessage] = field(default_factory=list)


class AgentRegistry:
    """Central registry for agent identities and message mailboxes.

    Maps between string-based names (used by fleet-midi agents) and
    16-byte binary UUIDs (used in the A2A wire protocol).
    """

    def __init__(self):
        self._agents: Dict[bytes, AgentInfo] = {}        # agent_id -> info
        self._by_name: Dict[str, bytes] = {}              # name -> agent_id
        self._lock = threading.Lock()

    # ── Registration ─────────────────────────────────────────────

    def register(
        self,
        agent_id: bytes,
        name: str,
        domain: str,
    ) -> bytes:
        """Register an agent with its 16-byte UUID.

        Args:
            agent_id: 16-byte UUID.
            name: Human-readable agent name (e.g. 'alice').
            domain: Fleet domain (e.g. 'chord', 'scale').

        Returns:
            The same agent_id (for chaining).
        """
        if len(agent_id) != 16:
            raise ValueError(f"agent_id must be 16 bytes, got {len(agent_id)}")
        with self._lock:
            info = AgentInfo(agent_id=agent_id, name=name, domain=domain)
            self._agents[agent_id] = info
            self._by_name[name] = agent_id
        return agent_id

    def register_str(
        self,
        agent_id_str: str,
        name: str,
        domain: str,
    ) -> bytes:
        """Register an agent using a string-based ID (auto-converted to UUID5)."""
        agent_id = uuid.uuid5(uuid.NAMESPACE_DNS, agent_id_str).bytes
        return self.register(agent_id, name, domain)

    def unregister(self, agent_id: bytes) -> bool:
        """Remove an agent from the registry.

        Args:
            agent_id: 16-byte UUID to remove.

        Returns:
            True if the agent was found and removed.
        """
        with self._lock:
            info = self._agents.pop(agent_id, None)
            if info is not None:
                self._by_name.pop(info.name, None)
                return True
            return False

    # ── Lookup ───────────────────────────────────────────────────

    def lookup(self, agent_id: bytes) -> Optional[AgentInfo]:
        """Look up an agent's info by its 16-byte UUID."""
        with self._lock:
            return self._agents.get(agent_id)

    def lookup_by_name(self, name: str) -> Optional[bytes]:
        """Look up an agent's 16-byte UUID by its human-readable name.

        Returns None if the name is not registered.
        """
        with self._lock:
            return self._by_name.get(name)

    def list_agents(self) -> List[Dict[str, Any]]:
        """Return a list of dicts describing all registered agents."""
        with self._lock:
            return [
                {
                    "agent_id": info.agent_id.hex(),
                    "name": info.name,
                    "domain": info.domain,
                    "inbox_size": len(info.inbox),
                }
                for info in self._agents.values()
            ]

    def agent_count(self) -> int:
        """Number of registered agents."""
        with self._lock:
            return len(self._agents)

    def has_agent(self, agent_id: bytes) -> bool:
        """Check whether a given agent UUID is registered."""
        with self._lock:
            return agent_id in self._agents

    def has_name(self, name: str) -> bool:
        """Check whether a given name is registered."""
        with self._lock:
            return name in self._by_name

    # ── Mailbox ──────────────────────────────────────────────────

    def pending_messages(self, agent_id: bytes) -> List[A2AMessage]:
        """Retrieve and drain all pending messages for an agent.

        Args:
            agent_id: 16-byte UUID of the target agent.

        Returns:
            List of undelivered A2AMessages (empty if none or unknown agent).
        """
        with self._lock:
            info = self._agents.get(agent_id)
            if info is None:
                return []
            messages = info.inbox[:]
            info.inbox.clear()
            return messages

    def deliver(self, message: A2AMessage) -> bool:
        """Deliver a message into the receiver's mailbox.

        Args:
            message: The A2AMessage to deliver.

        Returns:
            True if the receiver was found and the message was delivered.
        """
        with self._lock:
            info = self._agents.get(message.receiver)
            if info is None:
                return False
            info.inbox.append(message)
            return True


# ──────────────────────────────────────────────────────────────────────
# Conversation Tracking
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Conversation:
    """Tracks a chain of messages sharing the same conversation_id."""
    conversation_id: bytes
    messages: List[A2AMessage] = field(default_factory=list)
    replies: Dict[bytes, List[A2AMessage]] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


# ──────────────────────────────────────────────────────────────────────
# Character Integration — trust score modifiers from fleet stats
# ──────────────────────────────────────────────────────────────────────

def _import_agent_character() -> Any:
    """Lazy-import AgentCharacter to avoid circular / missing dep issues."""
    import sys
    sys.path.insert(0, '/home/ubuntu/.openclaw/workspace/fleet-characters')
    from fleet_characters.agent_profile import AgentCharacter  # noqa: F401
    return AgentCharacter


def stat_to_trust_bias(agent_char: Any, message_type: MessageType) -> float:
    """Compute a trust-bias modifier from an AgentCharacter's stats.

    The modifier depends on both the agent's stat profile and the
    message type, mirroring the role-playing flavour of the character
    system.

    Rules:
        - High Charisma (>15) → +0.2 general bonus (persuasive).
        - High Wisdom (>15) → more cautious: reduces trust for Ask
          messages (-0.15) since the agent is wary of requests.
        - High Constitution (>15) → trust decays slower: +0.1
          baseline (reliable).
        - High Perception (>15) → +0.05 for Tell (they listen well)
        - Low Wisdom (<8) → +0.1 gullibility bonus for Ask.
        - Low Perception (<8) → -0.1 for Tell (miss details).

    Returns:
        A float modifier (usually in [-0.3, 0.5]).
    """
    stats = agent_char.stats
    bias = 0.0

    # Charisma — general persuasiveness
    if stats.charisma > 15:
        bias += 0.2
    elif stats.charisma < 8:
        bias -= 0.1

    # Wisdom — caution / calibration
    if stats.wisdom > 15:
        if message_type == MessageType.Ask:
            bias -= 0.15  # cautious about answering requests
        elif message_type == MessageType.Tell:
            bias += 0.05   # wise enough to value information
    elif stats.wisdom < 8:
        bias += 0.1  # gullible

    # Constitution — reliability
    if stats.constitution > 15:
        bias += 0.1
    elif stats.constitution < 8:
        bias -= 0.1

    # Perception — attentiveness
    if stats.perception > 15:
        if message_type == MessageType.Tell:
            bias += 0.05
    elif stats.perception < 8:
        if message_type == MessageType.Tell:
            bias -= 0.1

    # Clamp to a reasonable range
    return max(-0.5, min(0.5, bias))


def character_influence(
    message: A2AMessage,
    agent_char: Any,
) -> float:
    """Apply an AgentCharacter's stats to modify a message's trust_score.

    Returns the modified trust_score (clamped to [0.0, 1.0]).

    Steps:
        1. Start from the message's current trust_score.
        2. Apply stat_to_trust_bias() based on the character and
           message type.
        3. If message is Broadcast, also modulate by the character's
           integration_score (specialised agents trust broadcasts less).
        4. If the character has a class with a trust affinity, apply it.
        5. Clamp result to [0.0, 1.0].
    """
    score = message.trust_score

    # Step 1: stat-based bias
    bias = stat_to_trust_bias(agent_char, message.message_type)
    score += bias

    # Step 2: Broadcast gets integration-score modulation
    if message.message_type == MessageType.Broadcast:
        integration = agent_char.integration_score
        # Specialised agents (low integration) are more sceptical of
        # broadcast messages. Generalists trust broadcasts more.
        score += (integration - 0.5) * 0.2
        # Integration ranges 0.3..0.9 → this adds -0.04..+0.08

    # Step 3: Class-based trust affinity
    class_name = agent_char.current_class.name
    class_affinity = _CLASS_TRUST_AFFINITY.get(class_name, 0.0)
    score += class_affinity

    return max(0.0, min(1.0, score))


# Class-based trust affinities — how each class naturally biases trust
_CLASS_TRUST_AFFINITY: Dict[str, float] = {
    "Sage": 0.05,           # Trusts appropriately; slight positive
    "Diplomat": 0.1,        # Social, tends to trust more
    "Bard": 0.05,           # Charismatic, open
    "Guardian": -0.1,       # Protective, sceptical
    "Warden": -0.05,        # Controls access, cautious
    "Infiltrator": -0.1,    # Suspicious by nature
    "Fleet Commander": 0.0, # Neutral, assesses each case
    "Avatar": 0.1,          # Legendary — confident, trusting
}


# ──────────────────────────────────────────────────────────────────────
# SwarmRouter — A2A message routing and orchestration
# ──────────────────────────────────────────────────────────────────────

class SwarmRouter:
    """Routes A2A messages between agents in the fleet.

    Combines an AgentRegistry (for identities and mailboxes) with
    conversation tracking and character-influenced trust scoring.
    Supports Tell, Ask, Delegate, and Broadcast patterns.
    """

    def __init__(self, registry: Optional[AgentRegistry] = None):
        self.registry = registry or AgentRegistry()
        self._conversations: Dict[bytes, Conversation] = {}
        self._conv_lock = threading.Lock()
        self._reply_events: Dict[bytes, threading.Event] = {}
        self._reply_store: Dict[bytes, List[A2AMessage]] = {}
        self._characters: Dict[bytes, Any] = {}  # agent_id -> AgentCharacter
        self._lock = threading.Lock()

    # ── Agent Registration ────────────────────────────────────────

    def register_agent(
        self,
        agent_id_str: str,
        name: str,
        domain: str,
        character: Optional[Any] = None,
    ) -> bytes:
        """Register an agent with a string ID (auto-converted to UUID5).

        Args:
            agent_id_str: String identifier (e.g. 'agent-1').
            name: Human-readable name (e.g. 'alice').
            domain: Fleet domain (e.g. 'chord').
            character: Optional AgentCharacter instance for trust influence.

        Returns:
            16-byte UUID assigned to this agent.
        """
        agent_id = self.registry.register_str(agent_id_str, name, domain)
        if character is not None:
            with self._lock:
                self._characters[agent_id] = character
        return agent_id

    def register_agent_bytes(
        self,
        agent_id: bytes,
        name: str,
        domain: str,
        character: Optional[Any] = None,
    ) -> bytes:
        """Register an agent with an explicit 16-byte UUID."""
        self.registry.register(agent_id, name, domain)
        if character is not None:
            with self._lock:
                self._characters[agent_id] = character
        return agent_id

    def unregister_agent(self, agent_id_str: str) -> bool:
        """Remove a previously registered agent by its string ID."""
        agent_id = uuid.uuid5(uuid.NAMESPACE_DNS, agent_id_str).bytes
        with self._lock:
            self._characters.pop(agent_id, None)
        return self.registry.unregister(agent_id)

    def set_character(self, agent_id: bytes, character: Any) -> None:
        """Attach an AgentCharacter to an agent for trust influence."""
        with self._lock:
            self._characters[agent_id] = character

    def get_character(self, agent_id: bytes) -> Optional[Any]:
        """Get the AgentCharacter attached to an agent, if any."""
        with self._lock:
            return self._characters.get(agent_id)

    def _resolve_agent_id(self, name_or_id: str) -> Optional[bytes]:
        """Resolve a name or string ID to a 16-byte UUID.

        First checks the registry by name. Falls back to generating
        a UUID5 from the string.
        """
        agent_id = self.registry.lookup_by_name(name_or_id)
        if agent_id is not None:
            return agent_id
        # Try as a UUID5 of the string (for agent_id_str style)
        candidate = uuid.UUID(bytes=uuid.uuid5(uuid.NAMESPACE_DNS, name_or_id).bytes).bytes
        if self.registry.has_agent(candidate):
            return candidate
        return None

    # ── Message Construction Helpers ──────────────────────────────

    def _new_message(
        self,
        sender: bytes,
        receiver: bytes,
        msg_type: MessageType,
        payload: bytes,
    ) -> A2AMessage:
        """Create a new message with character-influenced trust score."""
        msg = A2AMessage.new(
            sender=sender,
            receiver=receiver,
            msg_type=msg_type,
            payload=payload,
        )
        # Apply character influence if sender has one
        with self._lock:
            char = self._characters.get(sender)
        if char is not None:
            msg.trust_score = character_influence(msg, char)
        return msg

    # ── Routing ───────────────────────────────────────────────────

    def route(self, message: A2AMessage) -> bool:
        """Route a message to its receiver's mailbox.

        Records the message in the conversation history and, if the
        message_type is Ask, sets up a reply tracking event so that
        wait_for_reply can block on it.

        Args:
            message: The A2AMessage to route.

        Returns:
            True if the receiver was found and message delivered.
        """
        # Record in conversation
        conv_id = message.conversation_id
        with self._conv_lock:
            if conv_id not in self._conversations:
                self._conversations[conv_id] = Conversation(
                    conversation_id=conv_id,
                )
            self._conversations[conv_id].messages.append(message)

        delivered = self.registry.deliver(message)

        # If this is an Ask and was delivered, set up reply tracking
        if delivered and message.message_type == MessageType.Ask:
            with self._lock:
                if conv_id not in self._reply_store:
                    self._reply_store[conv_id] = []
                    self._reply_events[conv_id] = threading.Event()

        return delivered

    def broadcast(
        self,
        sender: bytes,
        payload: bytes,
        exclude_sender: bool = True,
    ) -> int:
        """Broadcast a message to all agents except the sender.

        Args:
            sender: 16-byte UUID of the sending agent.
            payload: Message payload bytes.
            exclude_sender: If True, skip delivering to the sender.

        Returns:
            Number of agents the message was delivered to.
        """
        count = 0
        conv_id = uuid.uuid4().bytes
        for info in self.registry._agents.values():
            if exclude_sender and info.agent_id == sender:
                continue
            msg = A2AMessage.new(
                sender=sender,
                receiver=info.agent_id,
                msg_type=MessageType.Broadcast,
                payload=payload,
                conversation_id=conv_id,
            )
            # Apply character influence on trust for the sender
            with self._lock:
                char = self._characters.get(sender)
            if char is not None:
                msg.trust_score = character_influence(msg, char)
            if self.route(msg):
                count += 1
        return count

    def tell(
        self,
        sender_name: str,
        receiver_name: str,
        payload: bytes,
    ) -> Optional[A2AMessage]:
        """Create and route a Tell message between named agents.

        Args:
            sender_name: Name or string ID of the sender.
            receiver_name: Name or string ID of the receiver.
            payload: Message payload bytes.

        Returns:
            The routed A2AMessage, or None if either agent is unknown.
        """
        sender = self._resolve_agent_id(sender_name)
        receiver = self._resolve_agent_id(receiver_name)
        if sender is None or receiver is None:
            return None
        msg = self._new_message(sender, receiver, MessageType.Tell, payload)
        self.route(msg)
        return msg

    def ask(
        self,
        sender_name: str,
        receiver_name: str,
        payload: bytes,
    ) -> Optional[A2AMessage]:
        """Create and route an Ask message between named agents.

        Also sets up reply tracking so wait_for_reply() can block.

        Args:
            sender_name: Name or string ID of the sender.
            receiver_name: Name or string ID of the receiver.
            payload: Message payload bytes.

        Returns:
            The routed A2AMessage, or None if either agent is unknown.
        """
        sender = self._resolve_agent_id(sender_name)
        receiver = self._resolve_agent_id(receiver_name)
        if sender is None or receiver is None:
            return None
        msg = self._new_message(sender, receiver, MessageType.Ask, payload)
        self.route(msg)
        return msg

    def delegate(
        self,
        sender_name: str,
        receiver_name: str,
        payload: bytes,
        trust_range: Tuple[float, float] = (0.0, 1.0),
    ) -> Optional[A2AMessage]:
        """Create and route a Delegate message between named agents.

        Delegation implies a transfer of authority. The trust_score
        is set to the midpoint of trust_range before character influence
        is applied.

        Args:
            sender_name: Name or string ID of the sender.
            receiver_name: Name or string ID of the receiver.
            payload: Message payload bytes.
            trust_range: (min, max) trust constraint for this delegation.

        Returns:
            The routed A2AMessage, or None if either agent is unknown.
        """
        sender = self._resolve_agent_id(sender_name)
        receiver = self._resolve_agent_id(receiver_name)
        if sender is None or receiver is None:
            return None
        mid_trust = (trust_range[0] + trust_range[1]) / 2.0
        msg = A2AMessage.new(
            sender=sender,
            receiver=receiver,
            msg_type=MessageType.Delegate,
            payload=payload,
            trust_score=mid_trust,
        )
        with self._lock:
            char = self._characters.get(sender)
        if char is not None:
            msg.trust_score = character_influence(msg, char)
        self.route(msg)
        return msg

    def reply(
        self,
        to_message: A2AMessage,
        payload: bytes,
        trust_score: Optional[float] = None,
    ) -> Optional[A2AMessage]:
        """Send a reply (as a Tell) to the sender of a previous message.

        Args:
            to_message: The message being replied to (swaps sender/receiver).
            payload: Reply payload.
            trust_score: Override trust score (default: to_message.trust_score).

        Returns:
            The reply A2AMessage, or None if sender/receiver don't exist.
        """
        reply = A2AMessage.new(
            sender=to_message.receiver,
            receiver=to_message.sender,
            msg_type=MessageType.Tell,
            payload=payload,
            conversation_id=to_message.conversation_id,
            trust_score=trust_score if trust_score is not None else to_message.trust_score,
        )
        if not self.route(reply):
            return None

        # Store reply for waiters
        with self._lock:
            conv_id = to_message.conversation_id
            if conv_id in self._reply_store:
                self._reply_store[conv_id].append(reply)
                event = self._reply_events.get(conv_id)
                if event is not None:
                    event.set()

        return reply

    # ── Reply Awaiting ────────────────────────────────────────────

    def wait_for_reply(
        self,
        conversation_id: bytes,
        timeout: float = 5.0,
    ) -> Optional[A2AMessage]:
        """Block until a reply arrives for the given conversation.

        Args:
            conversation_id: 16-byte conversation UUID to wait on.
            timeout: Maximum seconds to wait (default 5.0).

        Returns:
            The first reply A2AMessage, or None if timeout expires.
        """
        with self._lock:
            if conversation_id not in self._reply_events:
                self._reply_events[conversation_id] = threading.Event()
                self._reply_store[conversation_id] = []
            event = self._reply_events[conversation_id]

        event.wait(timeout=timeout)

        with self._lock:
            replies = self._reply_store.get(conversation_id, [])
            if replies:
                return replies[0]
        return None

    # ── Conversation Access ───────────────────────────────────────

    def get_conversation(self, conversation_id: bytes) -> Optional[Conversation]:
        """Get the full conversation history for a given conversation_id."""
        with self._conv_lock:
            return self._conversations.get(conversation_id)

    def list_conversations(self) -> List[Dict[str, Any]]:
        """List all tracked conversations with basic metadata."""
        with self._conv_lock:
            return [
                {
                    "conversation_id": cid.hex(),
                    "message_count": len(conv.messages),
                    "age_seconds": time.time() - conv.created_at,
                }
                for cid, conv in self._conversations.items()
            ]

    def conversation_messages(self, conversation_id: bytes) -> List[A2AMessage]:
        """Get all messages in a conversation."""
        conv = self.get_conversation(conversation_id)
        if conv is None:
            return []
        return conv.messages[:]

    # ── Status / Info ─────────────────────────────────────────────

    def status(self) -> Dict[str, Any]:
        """Return a snapshot of the router's state."""
        return {
            "agents": self.registry.agent_count(),
            "conversations": len(self._conversations),
            "pending_reply_awaiters": len(self._reply_events),
        }
