import copy
import threading
import unittest

from pricebot.fulfillment import apply_fulfillment, plan_fulfillment
from pricebot.inventory import Inventory


class FulfillmentTests(unittest.TestCase):
    def test_plan_fulfillment_deterministic(self):
        wh1 = Inventory({"A": 5, "B": 2})
        wh2 = Inventory({"A": 10, "B": 10})
        warehouses = {"wh_B": wh2, "wh_A": wh1}  # unsorted dict keys

        lines = [{"sku": "B", "quantity": 3}, {"sku": "A", "quantity": 6}]
        plan = plan_fulfillment("ord_1", lines, warehouses)

        # Warehouses sorted: wh_A then wh_B
        # SKUs sorted: A then B
        # For A: req 6. wh_A has 5, takes 5. wh_B takes 1.
        # For B: req 3. wh_A has 2 (after reserve? wait, available check considers current stock), wh_A has 2, takes 2. wh_B takes 1.
        self.assertEqual(plan["order_id"], "ord_1")
        self.assertIn("plan_id", plan)
        self.assertEqual(plan["allocations"]["wh_A"]["A"], 5)
        self.assertEqual(plan["allocations"]["wh_B"]["A"], 1)
        self.assertEqual(plan["allocations"]["wh_A"]["B"], 2)
        self.assertEqual(plan["allocations"]["wh_B"]["B"], 1)

    def test_plan_fulfillment_insufficient_stock(self):
        wh1 = Inventory({"A": 2})
        with self.assertRaises(ValueError):
            plan_fulfillment("ord_2", {"A": 10}, {"wh1": wh1})

    def test_plan_fulfillment_immutability(self):
        wh1 = Inventory({"A": 10})
        warehouses = {"wh1": wh1}
        lines = [{"sku": "A", "quantity": 5}]
        orig_wh1 = copy.deepcopy(wh1.snapshot())
        orig_lines = copy.deepcopy(lines)

        plan_fulfillment("ord_3", lines, warehouses)
        self.assertEqual(wh1.snapshot(), orig_wh1)
        self.assertEqual(lines, orig_lines)

    def test_apply_fulfillment_success(self):
        wh1 = Inventory({"A": 10})
        wh2 = Inventory({"B": 10})
        warehouses = {"wh1": wh1, "wh2": wh2}
        plan = plan_fulfillment("ord_4", [("A", 3), ("B", 4)], warehouses)

        plan_id = apply_fulfillment(plan, warehouses)
        self.assertEqual(plan_id, plan["plan_id"])
        self.assertIn(f"{plan_id}_wh1", wh1.reservations)
        self.assertIn(f"{plan_id}_wh2", wh2.reservations)

    def test_apply_fulfillment_rollback(self):
        wh1 = Inventory({"A": 10})
        wh2 = Inventory({"B": 2})  # insufficient for 5
        warehouses = {"wh1": wh1, "wh2": wh2}
        
        # Craft a malformed plan or modified plan where second warehouse fails reservation
        plan = plan_fulfillment("ord_5", [("A", 5), ("B", 2)], {"wh1": wh1, "wh2": wh2})
        # Modify plan allocation for wh2 to request more than available after creation
        plan["allocations"]["wh2"]["B"] = 10

        with self.assertRaises(ValueError):
            apply_fulfillment(plan, warehouses)

        # Verify wh1 reservation was rolled back (no active reservation)
        active_res = [r for r in wh1.reservations.values() if r["status"] == "reserved"]
        self.assertEqual(len(active_res), 0)

    def test_apply_fulfillment_idempotent(self):
        wh1 = Inventory({"A": 10})
        warehouses = {"wh1": wh1}
        plan = plan_fulfillment("ord_6", {"A": 4}, warehouses)

        apply_fulfillment(plan, warehouses)
        # Applying identical plan again should succeed (idempotent)
        apply_fulfillment(plan, warehouses)

    def test_apply_fulfillment_conflict(self):
        wh1 = Inventory({"A": 10})
        warehouses = {"wh1": wh1}
        plan = plan_fulfillment("ord_7", {"A": 4}, warehouses)
        apply_fulfillment(plan, warehouses)

        # Modify plan items for conflict
        plan_conf = copy.deepcopy(plan)
        plan_conf["allocations"]["wh1"]["A"] = 6
        with self.assertRaises(ValueError):
            apply_fulfillment(plan_conf, warehouses)

    def test_multi_warehouse_complex(self):
        whs = {
            "w1": Inventory({"A": 2, "B": 2}),
            "w2": Inventory({"A": 5, "B": 5}),
            "w3": Inventory({"A": 10, "B": 10})
        }
        plan = plan_fulfillment("ord_complex", {"A": 6, "B": 4}, whs)
        apply_fulfillment(plan, whs)
        self.assertEqual(whs["w1"].reservations[f"{plan['plan_id']}_w1"]["items"], {"A": 2, "B": 2})
        self.assertEqual(whs["w2"].reservations[f"{plan['plan_id']}_w2"]["items"], {"A": 4, "B": 2})

    def test_concurrency_simulation(self):
        wh1 = Inventory({"A": 100})
        warehouses = {"wh1": wh1}
        errors = []

        def worker():
            try:
                plan = plan_fulfillment("ord_c", {"A": 10}, warehouses)
                apply_fulfillment(plan, warehouses)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 5 threads * 10 = 50 reserved out of 100
        self.assertEqual(len(errors), 0)
        self.assertEqual(wh1.on_hand["A"], 100)

    def test_idempotent_plan_retry_missing_warehouse_preserves_reservation(self):
        wh1 = Inventory({"A": 2})
        wh2 = Inventory({"B": 2})
        warehouses = {"wh1": wh1, "wh2": wh2}
        plan = plan_fulfillment("order1", [{"sku": "A", "quantity": 1}, {"sku": "B", "quantity": 1}], warehouses)
        apply_fulfillment(plan, warehouses)
        before_wh1 = wh1.snapshot()
        with self.assertRaises(ValueError):
            apply_fulfillment(plan, {"wh1": wh1})
        self.assertEqual(wh1.snapshot(), before_wh1)
        res_id = f"{plan['plan_id']}_wh1"
        self.assertEqual(wh1.reservations[res_id]["status"], "reserved")


if __name__ == "__main__":
    unittest.main()
