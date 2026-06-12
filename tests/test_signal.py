#!/usr/bin/env python3
"""
A2A signal tests for flux_bridge.

Tests binary roundtrip of A2AMessage, Tell/Ask with payloads,
and conversation_id chaining.

Usage:
    python3 -c "from flux_bridge.tests.test_signal import *"
"""

import sys
import os
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from flux_bridge.signal_router import A2AMessage, MessageType

PASS = 0
FAIL = 0


def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        msg = f"  ❌ {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)


# ---------------------------------------------------------------------------
# 1.  Message binary roundtrip
# ---------------------------------------------------------------------------

def test_message_binary_roundtrip():
    """A2AMessage → to_bytes → from_bytes → identical fields"""
    sender = uuid.uuid4().bytes
    receiver = uuid.uuid4().bytes
    conv_id = uuid.uuid4().bytes
    payload = b"hello world"

    msg = A2AMessage(
        sender=sender,
        receiver=receiver,
        conversation_id=conv_id,
        message_type=MessageType.Ask,
        payload=payload,
        trust_score=0.75,
    )

    wire = msg.to_bytes()
    restored = A2AMessage.from_bytes(wire)

    check(
        "from_bytes returns a message (not None)",
        restored is not None,
    )
    if restored is None:
        return

    check(
        "sender preserved",
        restored.sender == sender,
        f"got {restored.sender.hex()[:16]}",
    )
    check(
        "receiver preserved",
        restored.receiver == receiver,
    )
    check(
        "conversation_id preserved",
        restored.conversation_id == conv_id,
    )
    check(
        "message_type preserved",
        restored.message_type == MessageType.Ask,
        f"got {restored.message_type}",
    )
    check(
        "payload preserved",
        restored.payload == payload,
        f"got {restored.payload!r}",
    )
    check(
        "trust_score preserved",
        restored.trust_score == 0.75,
        f"got {restored.trust_score}",
    )

    # Verify the serialised bytes match expected layout
    check(
        "wire format starts with sender",
        wire[:16] == sender,
    )
    check(
        "wire format has receiver",
        wire[16:32] == receiver,
    )
    check(
        "wire format has conversation_id",
        wire[32:48] == conv_id,
    )
    check(
        "wire format msg_type byte = 2 (Ask)",
        wire[48] == 2,
        f"got {wire[48]}",
    )
    # payload_len LE at [49..51]
    expected_len = len(payload)
    check(
        "wire format payload length correct",
        wire[49] == (expected_len & 0xFF) and wire[50] == (expected_len >> 8),
        f"got [{wire[49]}, {wire[50]}]",
    )


# ---------------------------------------------------------------------------
# 2.  Tell with empty payload
# ---------------------------------------------------------------------------

def test_tell_no_payload():
    """Tell with empty payload serialises and roundtrips correctly"""
    sender = uuid.uuid4().bytes
    receiver = uuid.uuid4().bytes

    msg = A2AMessage.new(
        sender=sender,
        receiver=receiver,
        msg_type=MessageType.Tell,
        payload=b"",
    )

    wire = msg.to_bytes()
    restored = A2AMessage.from_bytes(wire)

    check(
        "Tell with empty payload roundtrips",
        restored is not None,
    )
    if restored is None:
        return

    check(
        "Tell message_type preserved",
        restored.message_type == MessageType.Tell,
        f"got {restored.message_type}",
    )
    check(
        "empty payload preserved",
        restored.payload == b"",
        f"got {restored.payload!r}",
    )
    check(
        "sender preserved",
        restored.sender == sender,
    )
    check(
        "receiver preserved",
        restored.receiver == receiver,
    )

    # Minimum wire size = 55 bytes (16+16+16+1+2+0+4)
    check(
        "wire format minimum size (55 bytes)",
        len(wire) == 55,
        f"got {len(wire)}",
    )


# ---------------------------------------------------------------------------
# 3.  Ask with payload
# ---------------------------------------------------------------------------

def test_ask_with_payload():
    """Ask with JSON bytes payload roundtrips correctly"""
    sender = uuid.uuid4().bytes
    receiver = uuid.uuid4().bytes
    payload = b'{"query": "status", "agent": "node-3"}'

    msg = A2AMessage.new(
        sender=sender,
        receiver=receiver,
        msg_type=MessageType.Ask,
        payload=payload,
    )

    wire = msg.to_bytes()
    restored = A2AMessage.from_bytes(wire)

    check(
        "Ask with JSON payload roundtrips",
        restored is not None,
    )
    if restored is None:
        return

    check(
        "Ask message_type correctly restored",
        restored.message_type == MessageType.Ask,
    )
    check(
        "JSON payload intact",
        restored.payload == payload,
        f"got {restored.payload!r}",
    )
    check(
        "message_type byte is 2 (Ask)",
        wire[48] == 2,
    )

    # Verify JSON payload length in wire format
    plen = len(payload)
    check(
        "payload length bytes correct",
        wire[49] == (plen & 0xFF) and wire[50] == (plen >> 8),
        f"got [{wire[49]}, {wire[50]}], expected len={plen}",
    )


# ---------------------------------------------------------------------------
# 4.  Conversation ID — chain of messages preserves conversation_id
# ---------------------------------------------------------------------------

def test_conversation_id():
    """Message chain preserves conversation_id across Tell/Ask/Delegate"""
    alice = uuid.uuid4().bytes
    bob = uuid.uuid4().bytes

    # ── Step 1: Alice asks Bob ──
    conv_id = uuid.uuid4().bytes
    ask_msg = A2AMessage.new(
        sender=alice,
        receiver=bob,
        msg_type=MessageType.Ask,
        payload=b"What is the status?",
        conversation_id=conv_id,
    )
    check(
        "ask_msg has the assigned conversation_id",
        ask_msg.conversation_id == conv_id,
    )

    # ── Step 2: Bob replies (Tell) with the same conversation_id ──
    reply_msg = A2AMessage.new(
        sender=bob,
        receiver=alice,
        msg_type=MessageType.Tell,
        payload=b"All systems nominal.",
        conversation_id=conv_id,
    )
    check(
        "reply shares the same conversation_id",
        reply_msg.conversation_id == conv_id,
    )

    # ── Step 3: Alice delegates to Charlie with same conversation_id ──
    charlie = uuid.uuid4().bytes
    delegate_msg = A2AMessage.new(
        sender=alice,
        receiver=charlie,
        msg_type=MessageType.Delegate,
        payload=b"Verify node status",
        conversation_id=conv_id,
    )
    check(
        "delegate preserves conversation_id",
        delegate_msg.conversation_id == conv_id,
    )

    # ── Step 4: All three messages roundtrip and keep conv_id ──
    for label, msg in [
        ("ask_msg", ask_msg),
        ("reply_msg", reply_msg),
        ("delegate_msg", delegate_msg),
    ]:
        restored = A2AMessage.from_bytes(msg.to_bytes())
        check(
            f"{label} binary roundtrip preserves conversation_id",
            restored is not None and restored.conversation_id == conv_id,
        )

    # ── Step 5: All three have the same conversation_id = chain ──
    ids = [ask_msg.conversation_id, reply_msg.conversation_id, delegate_msg.conversation_id]
    check(
        "all messages in chain share conversation_id",
        len(set(ids)) == 1,
    )

    # ── Step 6: conversation_id is 16 bytes ──
    check(
        "conversation_id is 16 bytes",
        len(conv_id) == 16,
        f"got {len(conv_id)} bytes",
    )

    # ── Step 7: A fresh message (no conv_id) generates a new one ──
    new_msg = A2AMessage.new(
        sender=alice,
        receiver=bob,
        msg_type=MessageType.Tell,
        payload=b"fresh",
    )
    check(
        "new message without conv_id generates one",
        new_msg.conversation_id != b"\x00" * 16,
    )
    check(
        "new conversation_id is different from previous",
        new_msg.conversation_id != conv_id,
    )


# ---------------------------------------------------------------------------
# Run all tests
# ---------------------------------------------------------------------------

TEST_FUNCTIONS = [
    test_message_binary_roundtrip,
    test_tell_no_payload,
    test_ask_with_payload,
    test_conversation_id,
]

print("\n=== A2A Signal Tests ===")
for fn in TEST_FUNCTIONS:
    fn()

print(f"\nResults: {PASS} passed, {FAIL} failed")
if FAIL:
    print("Some tests FAILED")
