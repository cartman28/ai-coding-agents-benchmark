"""Unit tests for fulfillment planning and atomic application subsystem."""

import random
import unittest

from pricebot.fulfillment import apply_fulfillment, plan_fulfillment
from pricebot.inventory import Inventory


class FulfillmentTests(unittest.TestCase):
    def test_plan_fulfillment_deterministic(self):
        warehouses = {
            "wh_b": Inventory({"SKU1": 5, "SKU2": 10}),
            "wh_a": Inventory({"SKU1": 5, "SKU2": 10}),
        }
        lines = [{"sku": "SKU2", "quantity": 3}, {"sku": "SKU1", "quantity": 7}]
        plan = plan_fulfillment("ord-123", lines, warehouses)
        
        # Warehouses sorted: wh_a then wh_b. SKUs sorted: SKU1 then SKU2.
        # SKU1 required: 7. wh_a has 5 (takes 5), wh_b has 5 (takes 2).
        # SKU2 required: 3. wh_a has 10 left (takes 3).
        self.assertEqual(plan["order_id"], "ord-123")
        self.assertEqual(plan["allocations"]["wh_a"], {"SKU1": 5, "SKU2": 3})
        self.assertEqual(plan["allocations"]["wh_b"], {"SKU1": 2})

    def test_plan_fulfillment_insufficient_stock(self):
        warehouses = {
            "wh_1": Inventory({"SKU1": 2})
        }
        lines = [{"sku": "SKU1", "quantity": 5}]
        original_lines = [{"sku": "SKU1", "quantity": 5}]
        with self.assertRaises(ValueError):
            plan_fulfillment("ord-123", lines, warehouses)
        # Ensure inputs not mutated
        self.assertEqual(lines, original_lines)

    def test_apply_fulfillment_success(self):
        inv_a = Inventory({"SKU1": 10})
        inv_b = Inventory({"SKU1": 10})
        warehouses = {"wh_a": inv_a, "wh_b": inv_b}
        lines = [{"sku": "SKU1", "quantity": 12}]
        plan = plan_fulfillment("ord-456", lines, {"wh_a": inv_a, "wh_b": inv_b})
        
        plan_id = apply_fulfillment(plan, {"wh_a": inv_a, "wh_b": inv_b})
        self.assertEqual(plan_id, plan["plan_id"])
        self.assertEqual(inv_a.snapshot()["on_hand"]["SKU1"], 10)  # reserved, not yet committed
        self.assertIn(f"fulfillment_{plan_id}_wh_a", inv_a.snapshot()["reservations"])

    def test_apply_fulfillment_rollback_on_failure(self):
        inv_a = Inventory({"SKU1": 5})
        inv_b = Inventory({"SKU1": 1})  # insufficient stock in wh_b during application
        
        # Plan assumes wh_b has enough if we bypass or if stock changed concurrently
        plan = {
            "plan_id": "plan_test_rollback",
            "order_id": "ord-789",
            "allocations": {
                "wh_a": {"SKU1": 3},
                "wh_b": {"SKU1": 5}  # wh_b only has 1 on hand!
            }
        }
        
        with self.assertRaises(ValueError):
            apply_fulfillment(plan, {"wh_a": inv_a, "wh_b": inv_b})
        
        # Check that wh_a reservation was rolled back (status is released or reservation is released)
        snap_a = inv_a.snapshot()
        res_id = f"fulfillment_plan_test_rollback_wh_a"
        self.assertEqual(snap_a["reservations"][res_id]["status"], "released")

    def test_apply_fulfillment_idempotency(self):
        inv_a = Inventory({"SKU1": 10})
        plan = plan_fulfillment("ord-idemp", [{"sku": "SKU1", "quantity": 4}], {"wh_a": inv_a})
        
        # Apply twice
        apply_fulfillment(plan, {"wh_a": inv_a})
        apply_fulfillment(plan, {"wh_a": inv_a})
        
        snap = inv_a.snapshot()
        # Should only have 1 active reservation, not double reserved
        active_count = sum(1 for r in snap["reservations"].values() if r["status"] == "reserved")
        self.assertEqual(active_count, 1)

    def test_plan_fulfillment_invalid_inputs(self):
        inv = Inventory({"SKU1": 10})
        with self.assertRaises(ValueError):
            plan_fulfillment("", [{"sku": "SKU1", "quantity": 1}], {"wh_1": inv})
        with self.assertRaises(ValueError):
            plan_fulfillment("ord-1", [], {"wh_1": inv})
        with self.assertRaises(ValueError):
            plan_fulfillment("ord-1", [{"sku": "SKU1", "quantity": True}], {"wh_1": inv})

    def test_plan_fulfillment_dict_warehouses(self):
        warehouses = {
            "wh_1": {"SKU1": 10}
        }
        lines = [{"sku": "SKU1", "quantity": 4}]
        plan = plan_fulfillment("ord-dict", lines, warehouses)
        self.assertEqual(plan["allocations"]["wh_1"]["SKU1"], 4)

    def test_random_seeded_fulfillment(self):
        rng = random.Random(123)
        whs = {f"wh_{i}": Inventory({f"SKU{j}": 50 for j in range(5)}) for i in range(3)}
        for i in range(50):
            lines = [{f"sku": f"SKU{rng.randrange(5)}", "quantity": rng.randrange(1, 10)}]
            try:
                plan = plan_fulfillment(f"ord_{i}", lines, whs)
                apply_fulfillment(plan, whs)
            except ValueError:
                pass


if __name__ == "__main__":
    unittest.main()
