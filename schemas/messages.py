"""Pydantic-style validation schemas for A2A messages and swarm config.

Uses dataclasses with validation (lightweight, no pydantic dependency).
For Frankenstruct / pydantic interop, each schema has a .model_dump()
method matching the pydantic v2 API.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, fields, asdict
from enum import IntEnum
from typing import Any, ClassVar, Dict, List, Optional, Tuple


# ──────────────────────────────────────────────────────────────────────
# Schema Validation Helpers
# ──────────────────────────────────────────────────────────────────────

class ValidationError(ValueError):
    """Raised when schema validation fails."""
    pass


def _validate_bytes16(value: bytes, label: str) -> None:
    if not isinstance(value, (bytes, bytearray)):
        raise ValidationError(f"{label} must be bytes, got {type(value).__name__}")
    if len(value) != 16:
        raise ValidationError(f"{label} must be exactly 16 bytes, got {len(value)}")


def _validate_u16_payload(value: bytes, label: str) -> None:
    if not isinstance(value, (bytes, bytearray)):
        raise ValidationError(f"{label} must be bytes, got {type(value).__name__}")
    if len(value) > 65535:
        raise ValidationError(f"{label} exceeds 65535 bytes ({len(value)})")


def _validate_trust_score(value: float) -> None:
    if not isinstance(value, (int, float)):
        raise ValidationError(f"trust_score must be numeric, got {type(value).__name__}")
    if value < 0.0 or value > 1.0:
        raise ValidationError(f"trust_score must be in [0.0, 1.0], got {value}")


# ──────────────────────────────────────────────────────────────────────
# A2AMessageSchema
# ──────────────────────────────────────────────────────────────────────

@dataclass
class A2AMessageSchema:
    """Schema for validating incoming A2A messages.

    Accepts both raw field input and dict-based construction.
    Fields mirror A2AMessage but add validation.
    """

    sender: bytes
    receiver: bytes
    conversation_id: bytes
    message_type: int  # 1=Tell, 2=Ask, 3=Delegate, 4=Broadcast
    payload: bytes
    trust_score: float = 0.5

    # Valid message type values
    VALID_TYPES: ClassVar[set] = {1, 2, 3, 4}

    def __post_init__(self):
        self.validate()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> A2AMessageSchema:
        """Construct from a dictionary, coercing hex strings to bytes."""
        kwargs = {}
        for f in fields(cls):
            if f.name in ("VALID_TYPES",):
                continue
            raw = data.get(f.name)
            if raw is None:
                if f.name == "trust_score":
                    kwargs[f.name] = 0.5
                else:
                    raise ValidationError(f"Missing required field: {f.name}")
            elif isinstance(raw, str) and (f.type == 'bytes' or f.type is bytes):
                # Support hex-encoded UUIDs and payloads
                kwargs[f.name] = bytes.fromhex(raw)
            elif f.name == "payload" and isinstance(raw, str):
                # Payload may come as hex string
                kwargs[f.name] = raw.encode("utf-8") if isinstance(raw, str) else raw
            else:
                kwargs[f.name] = raw
        return cls(**kwargs)

    def validate(self) -> None:
        """Run all validations. Raises ValidationError on failure."""
        _validate_bytes16(self.sender, "sender")
        _validate_bytes16(self.receiver, "receiver")
        _validate_bytes16(self.conversation_id, "conversation_id")
        if self.message_type not in self.VALID_TYPES:
            raise ValidationError(
                f"message_type must be 1-4, got {self.message_type}"
            )
        _validate_u16_payload(self.payload, "payload")
        _validate_trust_score(self.trust_score)

    def to_a2a_message(self):
        """Convert to an A2AMessage (lazy import to avoid cycles)."""
        from ..signal_router import A2AMessage, MessageType
        return A2AMessage(
            sender=self.sender,
            receiver=self.receiver,
            conversation_id=self.conversation_id,
            message_type=MessageType(self.message_type),
            payload=self.payload,
            trust_score=self.trust_score,
        )

    @classmethod
    def from_a2a_message(cls, msg: Any) -> A2AMessageSchema:
        """Construct from an A2AMessage instance."""
        return cls(
            sender=msg.sender,
            receiver=msg.receiver,
            conversation_id=msg.conversation_id,
            message_type=msg.message_type.value,
            payload=msg.payload,
            trust_score=msg.trust_score,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialise to dict with hex-encoded bytes."""
        return {
            "sender": self.sender.hex(),
            "receiver": self.receiver.hex(),
            "conversation_id": self.conversation_id.hex(),
            "message_type": self.message_type,
            "payload": self.payload.hex(),
            "trust_score": round(self.trust_score, 6),
        }

    def model_dump(self) -> Dict[str, Any]:
        """Pydantic v2-compatible serialisation."""
        return self.to_dict()


# ──────────────────────────────────────────────────────────────────────
# AgentInfoSchema
# ──────────────────────────────────────────────────────────────────────

@dataclass
class AgentInfoSchema:
    """Schema for validating agent info records."""

    agent_id: bytes
    name: str
    domain: str
    inbox_size: int = 0
    character_class: str = "Undefined"
    integration_score: float = 0.5
    trust_bias: float = 0.0

    def __post_init__(self):
        self.validate()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AgentInfoSchema:
        """Construct from a dictionary, supporting hex-encoded UUIDs."""
        kwargs = {}
        for f in fields(cls):
            raw = data.get(f.name)
            if raw is None:
                if f.default is not field:
                    kwargs[f.name] = f.default
                else:
                    raise ValidationError(f"Missing required field: {f.name}")
            elif f.name == "agent_id" and isinstance(raw, str):
                kwargs[f.name] = bytes.fromhex(raw) if len(raw) == 32 else raw.encode()
            else:
                kwargs[f.name] = raw
        return cls(**kwargs)

    def validate(self) -> None:
        if self.name is not None and not isinstance(self.name, str):
            raise ValidationError(f"name must be a string, got {type(self.name).__name__}")
        if self.agent_id is not None:
            _validate_bytes16(self.agent_id, "agent_id")
        if not isinstance(self.inbox_size, int) or self.inbox_size < 0:
            raise ValidationError(f"inbox_size must be a non-negative int, got {self.inbox_size}")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def model_dump(self) -> Dict[str, Any]:
        return self.to_dict()


# ──────────────────────────────────────────────────────────────────────
# SwarmConfigSchema
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SwarmConfigSchema:
    """Schema for validating swarm configuration."""

    name: str = "default_swarm"
    max_agents: int = 100
    trust_threshold: float = 0.3
    default_timeout: float = 5.0
    enable_broadcast: bool = True
    enable_character_influence: bool = True
    domains: List[str] = field(default_factory=lambda: ["chord", "scale", "melody"])

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.name, str) or len(self.name) == 0:
            raise ValidationError("name must be a non-empty string")
        if self.max_agents < 1:
            raise ValidationError(f"max_agents must be >= 1, got {self.max_agents}")
        if self.trust_threshold < 0.0 or self.trust_threshold > 1.0:
            raise ValidationError(
                f"trust_threshold must be in [0.0, 1.0], got {self.trust_threshold}"
            )
        if self.default_timeout < 0.0:
            raise ValidationError(
                f"default_timeout must be >= 0, got {self.default_timeout}"
            )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SwarmConfigSchema:
        return cls(**{f.name: data.get(f.name, f.default) for f in fields(cls)})

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def model_dump(self) -> Dict[str, Any]:
        return self.to_dict()
