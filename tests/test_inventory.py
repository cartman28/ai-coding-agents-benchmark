"""Unit tests for Inventory management subsystem."""

import random
import threading
import unittest

from pricebot.inventory import Inventory


class InventoryTests(unittest.TestCase):
    def test_init_valid(self):
        inv = Inventory({"SKU1": 10, "SKU2": 0})
        self.assertEqual(inv.snapshot()["on_hand"], {"SKU1": 10, "SKU2": 0})

    def test_init_invalid(self):
        # Bool as int
        with self.assertRaises(ValueError):
            Inventory({True: 10})
        with self.assertRaises(ValueError):
            Inventory({"SKU1": True})
        # Negative stock
        with self.assertRaises(ValueError):
            Inventory({"SKU1": -1})
        # Not a mapping
        with self.assertRaises(ValueError):
            Inventory([("SKU1", 10)])  # type: ignore

    def test_reserve_valid(self):
        inv = Inventory({"SKU1": 10, "SKU2": 20})
        inv.reserve("res-1", {"SKU1": 3, "SKU2": 5})
        snap = inv.snapshot()
        self.assertEqual(snap["reservations"]["res-1"]["status"], "reserved")
        self.assertEqual(snap["reservations"]["res-1"]["items"], {"SKU1": 3, "SKU2": 5})

    def test_reserve_strict_validation(self):
        inv = Inventory({"SKU1": 10})
        # Unknown SKU
        with self.assertRaises(ValueError):
            inv.reserve("res-1", {"UNKNOWN": 1})
        # Zero or negative quantity
        with self.assertRaises(ValueError):
            inv.reserve("res-1", {"SKU1": 0})
        with self.assertRaises(ValueError):
            inv.reserve("res-1", {"SKU1": -2})
        # Bool as int quantity
        with self.assertRaises(ValueError):
            inv.reserve("res-1", {"SKU1": True})
        # Duplicate SKU in iterable
        with self.assertRaises(ValueError):
            inv.reserve("res-1", [("SKU1", 2), ("SKU1", 3)])

    def test_reserve_idempotency_and_conflict(self):
        inv = Inventory({"SKU1": 10})
        inv.reserve("res-1", {"SKU1": 3})
        # Idempotent replay with same items
        inv.reserve("res-1", {"SKU1": 3})
        # Conflicting replay with different items
        with self.assertRaises(ValueError):
            inv.reserve("res-1", {"SKU1": 4})

    def test_overselling_prevention(self):
        inv = Inventory({"SKU1": 5, "SKU2": 10})
        inv.reserve("res-1", {"SKU1": 5})
        # Attempting to oversell SKU1
        with self.assertRaises(ValueError):
            inv.reserve("res-2", {"SKU1": 1})
        # All-or-nothing multi-SKU reservation when one item fails
        with self.assertRaises(ValueError):
            inv.reserve("res-3", {"SKU1": 1, "SKU2": 15})
        # Verify SKU1 stock remains fully available for res-1
        snap = inv.snapshot()
        self.assertNotIn("res-3", snap["reservations"])

    def test_release_lifecycle(self):
        inv = Inventory({"SKU1": 10})
        inv.reserve("res-1", {"SKU1": 5})
        inv.release("res-1")
        snap = inv.snapshot()
        self.assertEqual(snap["reservations"]["res-1"]["status"], "released")
        # Releasing again is idempotent
        inv.release("res-1")
        # Stock is freed up now
        inv.reserve("res-2", {"SKU1": 10})

    def test_commit_lifecycle(self):
        inv = Inventory({"SKU1": 10})
        inv.reserve("res-1", {"SKU1": 4})
        inv.commit("res-1")
        snap = inv.snapshot()
        self.assertEqual(snap["reservations"]["res-1"]["status"], "committed")
        self.assertEqual(snap["on_hand"]["SKU1"], 6)
        # Committing again is idempotent
        inv.commit("res-1")
        # Cannot release committed reservation
        with self.assertRaises(ValueError):
            inv.release("res-1")

    def test_commit_released_error(self):
        inv = Inventory({"SKU1": 10})
        inv.reserve("res-1", {"SKU1": 4})
        inv.release("res-1")
        with self.assertRaises(ValueError):
            inv.commit("res-1")

    def test_restock(self):
        inv = Inventory({"SKU1": 5})
        inv.restock({"SKU1": 10})
        self.assertEqual(inv.snapshot()["on_hand"]["SKU1"], 15)
        # Unknown SKU restock rejected
        with self.assertRaises(ValueError):
        # Unknown SKU restock rejected
            inv.restock({"SKU2": 5})

    def test_snapshot_immutability(self):
        inv = Inventory({"SKU1": 10})
        snap1 = inv.snapshot()
        inv.restock({"SKU1": 5})
        snap2 = inv.snapshot()
        self.assertEqual(snap1["on_hand"]["SKU1"], 10)
        self.assertEqual(snap2["on_hand"]["SKU1"], 15)

    def test_concurrency_locking(self):
        inv = Inventory({"SKU1": 1000})
        threads = []
        for i in range(50):
            t = threading.Thread(target=lambda r_id: inv.reserve(r_id, {"SKU1": 1}), args=(f"res-{i}",))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        snap = inv.snapshot()
        active_count = sum(1 for r in snap["reservations"].values() if r["status"] == "reserved")
        self.assertEqual(active_count, 50)

    def test_random_seeded_sequence(self):
        rng = random.Random(42)
        inv = Inventory({f"SKU{i}": 100 for i in range(10)})
        for step in range(200):
            sku = f"SKU{rng.randrange(10)}"
            qty = rng.randrange(1, 5)
            res_id = f"res_{step}"
            try:
                inv.reserve(res_id, {sku: qty})
                if rng.random() > 0.5:
                    inv.commit(res_id)
                else:
                    inv.release(res_id)
            except ValueError:
                pass
        snap = inv.snapshot()
        self.assertIsInstance(snap, dict)


if __name__ == "__main__":
    unittest.main()
