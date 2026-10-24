"""Independent Task 4 adversarial acceptance suite. Not authored by benchmark agents."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from pricebot.inventory import Inventory
from pricebot.fulfillment import plan_fulfillment, apply_fulfillment
from pricebot import journal


class IndependentInventoryTests(unittest.TestCase):
    def test_reserve_and_commit(self):
        inv = Inventory({"A": 5, "B": 3})
        inv.reserve("r1", {"A": 4, "B": 2})
        with self.assertRaises(ValueError):
            inv.reserve("r2", {"A": 2})
        inv.commit("r1")
        self.assertEqual(inv.snapshot()["on_hand"]["A"], 1)

    def test_duplicate_lines(self):
        inv = Inventory({"A": 10})
        with self.assertRaises(ValueError):
            inv.reserve("r", [("A", 1), ("A", 2)])

    def test_snapshot_independent(self):
        inv = Inventory({"A": 3})
        s = inv.snapshot()
        s["on_hand"]["A"] = 900
        self.assertEqual(inv.snapshot()["on_hand"]["A"], 3)

    def test_concurrent_reservation_no_oversell(self):
        inv = Inventory({"A": 1})
        barrier = threading.Barrier(16)
        successes = []
        def reserve(i):
            barrier.wait()
            try:
                inv.reserve(f"r{i}", {"A": 1})
                successes.append(i)
            except ValueError:
                pass
        ts = [threading.Thread(target=reserve, args=(i,)) for i in range(16)]
        for t in ts: t.start()
        for t in ts: t.join()
        self.assertEqual(len(successes), 1)

    def test_idempotent_plan_does_not_release_prior_reservation_on_later_failure(self):
        a = Inventory({"A": 2})
        b = Inventory({"B": 2})
        plan = plan_fulfillment("order1", [{"sku": "A", "quantity": 1}, {"sku": "B", "quantity": 1}], {"a": a, "b": b})
        apply_fulfillment(plan, {"a": a, "b": b})
        before = a.snapshot()
        with self.assertRaises(Exception):
            apply_fulfillment(plan, {"a": a})
        self.assertEqual(a.snapshot(), before, "Failed retry must not release a reservation from a previous successful call")

    def test_plan_is_deterministic(self):
        a, b = Inventory({"A": 2}), Inventory({"A": 3})
        p1 = plan_fulfillment("o", [{"sku": "A", "quantity": 4}], {"b": b, "a": a})
        p2 = plan_fulfillment("o", [{"sku": "A", "quantity": 4}], {"a": a, "b": b})
        self.assertEqual(p1, p2)
        self.assertEqual(p1["allocations"]["a"]["A"], 2)

    def test_invalid_bool_quantities(self):
        inv = Inventory({"A": 2})
        with self.assertRaises(ValueError):
            inv.reserve("r", {"A": True})

    def test_no_mutation_failed_plan(self):
        inv = Inventory({"A": 1})
        before = inv.snapshot()
        with self.assertRaises(ValueError):
            plan_fulfillment("o", [{"sku": "A", "quantity": 2}], {"a": inv})
        self.assertEqual(inv.snapshot(), before)


class IndependentJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "events.jsonl"
    def tearDown(self):
        self.tmp.cleanup()
    def _append(self, typ, data):
        if hasattr(journal, "load_journal"):
            entries = journal.load_journal(self.path)
            prev = entries[-1]["hash"] if entries else None
            return journal.append_journal(self.path, typ, data, prev_hash=prev, seq=len(entries)+1)
        return journal.append_journal(self.path, typ, data)

    def test_hash_chain_tampering(self):
        first_type = "initial_stock" if hasattr(journal, "load_journal") else "initialize"
        self._append(first_type, {"initial_stock": {"A": 3}})
        self._append("reserve", {"reservation_id": "r", "items": {"A": 1}})
        raw = self.path.read_text()
        self.path.write_text(raw.replace('"A":1', '"A":2'))
        with self.assertRaises(ValueError):
            journal.replay_journal(self.path)

    def test_journal_trailing_partial_line(self):
        first_type = "initial_stock" if hasattr(journal, "load_journal") else "initialize"
        self._append(first_type, {"initial_stock": {"A": 3}})
        with self.path.open("ab") as f:
            f.write(b'{"partial":')
        with self.assertRaises(ValueError):
            journal.replay_journal(self.path)

    def test_replay_valid_events(self):
        first_type = "initial_stock" if hasattr(journal, "load_journal") else "initialize"
        self._append(first_type, {"initial_stock": {"A": 3}})
        self._append("reserve", {"reservation_id": "r", "items": {"A": 2}})
        self._append("commit", {"reservation_id": "r"})
        self.assertEqual(journal.replay_journal(self.path).snapshot()["on_hand"]["A"], 1)

    def test_snapshot_corrupt_schema(self):
        path = Path(self.tmp.name) / "snapshot.json"
        path.write_text('{"on_hand":{"A":-9},"reservations":{}}')
        with self.assertRaises(ValueError):
            journal.load_snapshot(path)

    def test_snapshot_immutable_after_write(self):
        path = Path(self.tmp.name) / "snapshot.json"
        state = Inventory({"A": 3}).snapshot()
        journal.write_snapshot(path, state)
        state["on_hand"]["A"] = 99
        self.assertEqual(journal.load_snapshot(path)["on_hand"]["A"], 3)


if __name__ == "__main__":
    unittest.main()
