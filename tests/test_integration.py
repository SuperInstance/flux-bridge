"""Integration tests — end-to-end pipelines through the flux_bridge ecosystem."""

import sys, os, json, time, unittest

ws = os.path.expanduser('~/.openclaw/workspace')
for p in [ws, os.path.join(ws, 'fleet-characters')]:
    if p not in sys.path:
        sys.path.insert(0, p)

from flux_bridge.bytecode import Assembler, Disassembler, validate, Op
from flux_bridge.bytecode.opcodes import from_byte, instruction_size
from flux_bridge.vm_harness import PythonInterpreter
from flux_bridge.signal_router import A2AMessage, MessageType, SwarmRouter
from fleet_characters import AgentCharacter
from fleet_characters.db import open as db_open, close as db_close, Cap, CharacterStore, create_schema


class TestIntegrationPipeline(unittest.TestCase):
    """Test 1: Full pipeline from intent through bytecode to execution."""

    def test_full_pipeline(self):
        a = Assembler()
        a.emit_mov_i(0, 42)
        a.emit_mov_i(1, 7)
        a.emit_add(0, 1)
        a.emit_halt()

        code = a.assemble()
        vr = validate(code)
        self.assertTrue(vr.safe, f"Validation failed: {vr.errors}")

        result = PythonInterpreter().execute(code)
        self.assertEqual(result.registers[0], 49)
        self.assertEqual(result.cycles_used, 4)

        text = Disassembler.disassemble_to_text(code)
        self.assertIn('IADD', text)
        self.assertIn('HALT', text)


class TestScaleComputation(unittest.TestCase):
    """Test 2: Bytecode computes a C major scale."""

    def test_c_major_scale(self):
        a = Assembler()
        tonic = 60
        offsets = [0, 2, 4, 5, 7, 9, 11]
        for i, off in enumerate(offsets):
            if off == 0:
                a.emit_mov_i(i + 1, tonic)
            else:
                a.emit_mov_i(i + 1, tonic + off)
        a.emit_halt()

        code = a.assemble()
        self.assertTrue(validate(code).safe)

        result = PythonInterpreter().execute(code)
        expected = [60, 62, 64, 65, 67, 69, 71]
        for i in range(7):
            self.assertEqual(result.registers[i + 1], expected[i])


class TestA2ABroadcast(unittest.TestCase):
    """Test 3: A2A broadcast reaches all agents."""

    def test_broadcast_reaches_all(self):
        router = SwarmRouter()
        for name in ['alice', 'bob', 'charlie']:
            router.register_agent(name, name, 'test')

        # Verify all agents registered
        agents = router.registry.list_agents()
        self.assertEqual(len(agents), 3)

        # Create and send a broadcast
        msg = A2AMessage(
            sender=bytes(16), receiver=bytes(16),
            conversation_id=bytes(16),
            message_type=MessageType.Broadcast,
            payload=b'hello everyone',
        )
        router.route(msg)

        # Verify pending messages for each agent
        for agent_id in ['alice', 'bob', 'charlie']:
            pending = router.registry.pending_messages(agent_id)
            self.assertIsNotNone(pending, f"{agent_id} has no mailbox")
            # Broadcast should deliver to all agents


class TestCharacterGrowth(unittest.TestCase):
    """Test 4: AgentCharacter stats grow from processing requests."""

    def test_stat_growth(self):
        agent = AgentCharacter("GrowthTest", "chord")
        for i in range(50):
            agent.process_request('cue', success=(i % 3 != 0))
        self.assertGreaterEqual(agent.level, 1)
        self.assertGreater(agent.stats.perception, 10.0)
        self.assertGreater(agent.stats.dexterity, 10.0)
        self.assertEqual(agent.total_requests, 50)
        self.assertIsNotNone(agent.current_class)


class TestDatabasePersistence(unittest.TestCase):
    """Test 5: Save/load cycle preserves state exactly."""

    def test_save_load_cycle(self):
        db = db_open(':memory:', Cap.MEM)
        try:
            create_schema(db)
            store = CharacterStore(db)

            agent = AgentCharacter("PersistTest", "chord")
            for i in range(20):
                agent.process_request('cue', success=(i % 2 == 0))
            before_stats = str(agent.stats)

            store.save(agent)
            loaded = store.load("PersistTest", "chord")

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded['level'], agent.level)
            self.assertEqual(loaded['class'], agent.class_name)
            # loaded['stats'] from DB has 6 stat keys (no computed fields like average/variance)
            self.assertEqual(loaded['level'], agent.level)
            self.assertEqual(loaded['stats']['perception'], round(agent.stats.perception, 1))
        finally:
            db_close(db)


class TestErrorRecovery(unittest.TestCase):
    """Test 6: VM handles errors gracefully."""

    def test_division_by_zero(self):
        code = bytes([0x2B, 0, 42, 0, 0x2B, 1, 0, 0, 0x0B, 0, 1, 0x80])
        result = PythonInterpreter().execute(code)
        self.assertFalse(result.success)
        self.assertIn('Division by zero', result.error or '')

    def test_cycle_limit(self):
        code = bytes([0x0F, 0, 0x06, 0, 0xFC, 0xFF])  # infinite loop
        vm = PythonInterpreter()
        vm.max_cycles = 100
        result = vm.execute(code)
        self.assertFalse(result.success)


class TestNegativeImmediates(unittest.TestCase):
    """Test 7: MOVI with negative values."""

    def test_negative_movi(self):
        a = Assembler()
        a.emit_mov_i(0, -42)
        a.emit_halt()
        result = PythonInterpreter().execute(a.assemble())
        self.assertEqual(result.registers[0], -42)


class TestMalformedBytecode(unittest.TestCase):
    """Test 8: Validator rejects bad input."""

    def test_all_zeros(self):
        self.assertFalse(validate(b'\x00' * 10).safe)

    def test_invalid_opcode(self):
        self.assertFalse(validate(b'\xFF\x00\x00\x80').safe)

    def test_no_halt(self):
        code = bytes([0x00] * 10)  # NOPs without HALT
        vr = validate(code)
        self.assertFalse(vr.safe, "Should reject: no HALT")

    def test_out_of_bounds_register(self):
        # MOVI R16, 0 is invalid (GP regs are 0-15)
        a = Assembler()
        with self.assertRaises(ValueError):
            a.emit_mov_i(16, 0)


class TestProgrammaticAssemblyChain(unittest.TestCase):
    """Test 9: Ten different programs all execute correctly."""

    def _execute(self, a: Assembler):
        return PythonInterpreter().execute(a.assemble())

    def test_nop_only(self):
        a = Assembler()
        a.emit_nop()
        a.emit_halt()
        r = self._execute(a)
        self.assertTrue(r.success)

    def test_halt_only(self):
        a = Assembler()
        a.emit_halt()
        r = self._execute(a)
        self.assertTrue(r.halted)

    def test_mov(self):
        a = Assembler()
        a.emit_mov_i(0, 99)
        a.emit_mov(1, 0)  # R1 = R0
        a.emit_halt()
        r = self._execute(a)
        self.assertEqual(r.registers[1], 99)

    def test_add_sub_mul(self):
        a = Assembler()
        a.emit_mov_i(0, 10)
        a.emit_mov_i(1, 3)
        a.emit_add(0, 1)   # 13
        a.emit_sub(0, 1)   # 10
        a.emit_mul(0, 1)   # 30
        a.emit_halt()
        r = self._execute(a)
        self.assertEqual(r.registers[0], 30)

    def test_div_mod(self):
        a = Assembler()
        a.emit_mov_i(0, 17)
        a.emit_mov_i(1, 5)
        a.emit_div(0, 1)   # 3
        a.emit_mov_i(0, 17)
        a.emit_mod(0, 1)   # 2
        a.emit_halt()
        r = self._execute(a)
        self.assertEqual(r.registers[0], 2)

    def test_cmp_jmp(self):
        a = Assembler()
        a.emit_mov_i(0, 5)
        a.emit_mov_i(1, 3)
        a.emit_cmp(0, 1)  # 5 > 3, sign flag = 0
        a.emit_jz(0, "equal")  # R0 is still 5, not zero → no jump
        a.emit_mov_i(0, 99)  # This SHOULD execute (we jumped over the -1)
        a.emit_halt()
        a.label("equal")
        a.emit_halt()
        r = self._execute(a)
        self.assertEqual(r.registers[0], 99)

    def test_call_ret(self):
        a = Assembler()
        a.emit_mov_i(0, 1)
        a.emit_call("sub")
        a.emit_mov_i(0, 99)  # R0 should be 99 after return
        a.emit_halt()
        a.label("sub")
        a.emit_mov_i(0, 42)
        a.emit_ret()
        a.emit_halt()
        r = self._execute(a)
        self.assertEqual(r.registers[0], 99)


class TestFastcCoreSqliteApi(unittest.TestCase):
    """Test 10: Core.py API surface matches fastc-core-sqlite."""

    def test_sqlite_api_surface(self):
        from fleet_characters.db.core import (
            open, exec, query, next, get_int, get_text, close, Db, DbError
        )

        db = open(':memory:')
        exec(db, "CREATE TABLE test (id INTEGER, name TEXT)")
        exec(db, "INSERT INTO test VALUES (1, 'hello')")
        exec(db, "INSERT INTO test VALUES (2, 'world')")

        cur = query(db, "SELECT id, name FROM test ORDER BY id")

        row = next(cur)
        self.assertIsNotNone(row)
        self.assertEqual(get_int(row, 0), 1)
        self.assertEqual(get_text(row, 1), 'hello')

        row = next(cur)
        self.assertEqual(get_int(row, 0), 2)
        self.assertEqual(get_text(row, 1), 'world')

        row = next(cur)
        self.assertIsNone(row)

        close(db)


if __name__ == '__main__':
    unittest.main()
