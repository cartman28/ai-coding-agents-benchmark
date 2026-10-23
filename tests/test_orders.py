import unittest
import copy

from pricebot.orders import create_order, transition_order, refund_order


class OrderTests(unittest.TestCase):
    def test_create_order_basic(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 2}]
        order = create_order(items, shipping_cents=500)
        self.assertEqual(order["status"], "draft")
        self.assertEqual(order["version"], 1)
        self.assertEqual(order["subtotal_cents"], 200)
        self.assertEqual(order["total_cents"], 700)
        self.assertTrue(order["order_id"].startswith("ord_"))

    def test_discount_ties_and_largest_remainder(self):
        # Two items with equal subtotals, test tie breaking by ascending SKU
        items = [
            {"sku": "Z_item", "unit_price_cents": 100, "quantity": 1},
            {"sku": "A_item", "unit_price_cents": 100, "quantity": 1},
        ]
        # 15% discount on 200 cents = 30 cents. Each gets 15 cents base (30*100//200 = 15).
        # What about uneven split? E.g. 10% discount on 300 cents = 30 cents.
        # Let's test items with subtotals 100 and 200. Total subtotal = 300.
        # Coupon: 10% -> 30 cents discount total.
        items2 = [
            {"sku": "ITEM2", "unit_price_cents": 200, "quantity": 1},
            {"sku": "ITEM1", "unit_price_cents": 100, "quantity": 1},
        ]
        order = create_order(items2, coupon={"type": "percent", "value": 10})
        self.assertEqual(order["discount_cents"], 30)
        # ITEM1 subtotal 100, ITEM2 subtotal 200.
        # base: ITEM1: 30*100//300 = 10. ITEM2: 30*200//300 = 20. Total = 30.

    def test_per_line_tax(self):
        items = [
            {"sku": "X", "unit_price_cents": 100, "quantity": 1},
            {"sku": "Y", "unit_price_cents": 100, "quantity": 1},
        ]
        # tax_rate_bps = 1000 (10%)
        order = create_order(items, tax_rate_bps=1000)
        self.assertEqual(order["tax_cents"], 20)
        for line in order["lines"]:
            self.assertEqual(line["tax_cents"], 10)

    def test_deterministic_order_id(self):
        items = [{"sku": "A", "unit_price_cents": 500, "quantity": 1}]
        o1 = create_order(items, shipping_cents=100)
        o2 = create_order(items, shipping_cents=100)
        self.assertEqual(o1["order_id"], o2["order_id"])

    def test_invalid_inputs_orders(self):
        # Empty items
        with self.assertRaises(ValueError):
            create_order([])
        # Invalid item types / duplicate SKUs
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 1}, {"sku": "A", "unit_price_cents": 50, "quantity": 2}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": True, "quantity": 1}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 0}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "", "unit_price_cents": 100, "quantity": 1}])
        # Invalid coupon
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], coupon={"type": "percent", "value": 101})
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], coupon={"type": "invalid", "value": 10})
        # Invalid tax & shipping
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], tax_rate_bps=10001)
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], shipping_cents=-1)

    def test_no_input_mutation(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        items_copy = copy.deepcopy(items)
        create_order(items)
        self.assertEqual(items, items_copy)

    def test_state_machine_transitions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o = create_order(items)
        o = transition_order(o, "submit")
        self.assertEqual(o["status"], "submitted")
        self.assertEqual(o["version"], 2)
        
        o = transition_order(o, "pay")
        self.assertEqual(o["status"], "paid")
        self.assertEqual(o["version"], 3)

        o = transition_order(o, "ship")
        self.assertEqual(o["status"], "shipped")
        self.assertEqual(o["version"], 4)

    def test_cancellation_transitions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o = create_order(items)
        o_cancel = transition_order(o, "cancel")
        self.assertEqual(o_cancel["status"], "cancelled")

        o2 = transition_order(create_order(items), "submit")
        o2_cancel = transition_order(o2, "cancel")
        self.assertEqual(o2_cancel["status"], "cancelled")

    def test_invalid_transitions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o = create_order(items)
        with self.assertRaises(ValueError):
            transition_order(o, "pay")
        with self.assertRaises(ValueError):
            transition_order(o, "ship")

    def test_stale_versions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o = create_order(items)
        with self.assertRaises(ValueError):
            transition_order(o, "submit", expected_version=2)

    def test_refunds_and_batch_invariance(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 4}]
        o = create_order(items)
        # Cannot refund draft or submitted
        with self.assertRaises(ValueError):
            refund_order(o)

        o = transition_order(transition_order(o, "submit"), "pay")
        
        # Test batch invariance: refund 2 units at once vs 1 unit then 1 unit
        o_batch1 = refund_order(o, quantities={"A": 2})
        self.assertEqual(o_batch1["refunded_cents"], 200)

        o_step1 = refund_order(o, quantities={"A": 1})
        o_step2 = refund_order(o_step1, quantities={"A": 1})
        self.assertEqual(o_step2["refunded_cents"], o_batch1["refunded_cents"])
        self.assertEqual(o_step2["version"], 5) # v1 created, v2 submit, v3 pay, v4 ref1, v5 ref2

    def test_zero_unit_and_excessive_refunds(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 2}]
        o = transition_order(transition_order(create_order(items), "submit"), "pay")
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"A": 0})
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"A": 3})
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"UNKNOWN": 1})


if __name__ == "__main__":
    unittest.main()
