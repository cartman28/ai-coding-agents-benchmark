import random
import unittest

from pricebot.inventory import Inventory


class InventoryTests(unittest.TestCase):
    def test_init_validation(self):
        # Valid init
        inv = Inventory({"A": 10, "B": 0})
        self.assertEqual(inv.on_hand, {"A": 10, "B": 0})

        # Invalid mappings / types / negative values
        with self.assertRaises(ValueError):
            Inventory("not a mapping")
        with self.assertRaises(ValueError):
            Inventory({123: 10})
        with self.assertRaises(ValueError):
            Inventory({"A": -1})
        with self.assertRaises(ValueError):
            Inventory({"A": True})  # bool as int
        with self.assertRaises(ValueError):
            Inventory({"A": "10"})

    def test_reserve_and_lifecycle(self):
        inv = Inventory({"A": 10, "B": 5})
        inv.reserve("res_1", {"A": 3, "B": 2})
        snap = inv.snapshot()
        self.assertEqual(snap["reservations"]["res_1"]["status"], "reserved")
        self.assertEqual(snap["reservations"]["res_1"]["items"], {"A": 3, "B": 2})

        # Release
        inv.release("res_1")
        self.assertEqual(inv.snapshot()["reservations"]["res_1"]["status"], "released")

        # New reservation after release using new ID
        inv.reserve("res_2", {"A": 4})
        inv.commit("res_2")
        self.assertEqual(inv.on_hand["A"], 6)
        self.assertEqual(inv.snapshot()["reservations"]["res_2"]["status"], "committed")

    def test_idempotent_reserve(self):
        inv = Inventory({"A": 10})
        inv.reserve("r1", {"A": 5})
        # Identical retry should succeed without error
        inv.reserve("r1", {"A": 5})
        inv.reserve("r1", [("A", 5)])

    def test_conflicting_reserve(self):
        inv = Inventory({"A": 10})
        inv.reserve("r1", {"A": 5})
        with self.assertRaises(ValueError):
            inv.reserve("r1", {"A": 6})
        with self.assertRaises(ValueError):
            inv.reserve("r1", {"A": 4})

        # Already committed/released with same ID should raise ValueError on re-reserve
        inv.commit("r1")
        with self.assertRaises(ValueError):
            inv.reserve("r1", {"A": 5})

    def test_overselling(self):
        inv = Inventory({"A": 5})
        inv.reserve("r1", {"A": 3})
        # Available is 2, requesting 3 should fail
        with self.assertRaises(ValueError):
            inv.reserve("r2", {"A": 3})
        # Requesting 2 succeeds
        inv.reserve("r2", {"A": 2})

    def test_all_or_nothing_multi_sku(self):
        inv = Inventory({"A": 10, "B": 2})
        # B has only 2, requesting 3 should fail atomically (neither A nor B reserved)
        with self.assertRaises(ValueError):
            inv.reserve("r1", {"A": 5, "B": 3})
        
        # Verify no reservation was created
        self.assertEqual(len(inv.reservations), 0)

        # Successful multi-sku reservation
        inv.reserve("r1", {"A": 5, "B": 2})
        self.assertEqual(len(inv.reservations), 1)

    def test_invalid_transitions(self):
        inv = Inventory({"A": 10})
        with self.assertRaises((KeyError, ValueError)):
            inv.release("nonexistent")
        with self.assertRaises((KeyError, ValueError)):
            inv.commit("nonexistent")

        inv.reserve("r1", {"A": 2})
        inv.release("r1")
        with self.assertRaises(ValueError):
            inv.release("r1")
        with self.assertRaises(ValueError):
            inv.commit("r1")

    def test_restock(self):
        inv = Inventory({"A": 5})
        inv.restock({"A": 5, "B": 10})
        self.assertEqual(inv.on_hand["A"], 10)
        self.assertEqual(inv.on_hand["B"], 10)

        with self.assertRaises(ValueError):
            inv.restock({"A": -1})
        with self.assertRaises(ValueError):
            inv.restock({"A": True})

    def test_snapshot_immutability(self):
        inv = Inventory({"A": 10})
        snap1 = inv.snapshot()
        inv.restock({"A": 5})
        snap2 = inv.snapshot()
        self.assertEqual(snap1["on_hand"]["A"], 10)
        self.assertEqual(snap2["on_hand"]["A"], 15)

    def test_random_seeded_sequence(self):
        rng = random.Random(42)
        inv = Inventory({"SKU1": 100, "SKU2": 100, "SKU3": 100})
        for i in range(50):
            op = rng.choice(["reserve", "release", "commit", "restock"])
            res_id = f"res_{rng.randint(1, 20)}"
            sku = f"SKU{rng.randint(1, 3)}"
            qty = rng.randint(1, 5)
            try:
                if op == "reserve":
                    inv.reserve(res_id, {sku: qty})
                elif op == "release":
                    inv.release(res_id)
                elif op == "commit":
                    inv.commit(res_id)
                elif op == "restock":
                    inv.restock({sku: qty})
            except (ValueError, KeyError):
                pass  # expected for invalid state transitions, missing reservations, or overselling


if __name__ == "__main__":
    unittest.main()
