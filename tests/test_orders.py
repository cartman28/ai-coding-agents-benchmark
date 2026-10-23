import copy
import unittest

from pricebot.orders import create_order, transition_order, refund_order


class OrderTests(unittest.TestCase):
    def test_create_order_basic(self):
        items = [{"sku": "A1", "unit_price_cents": 1000, "quantity": 2}]
        order = create_order(items, shipping_cents=500)
        self.assertEqual(order["version"], 1)
        self.assertEqual(order["status"], "draft")
        self.assertEqual(order["subtotal_cents"], 2000)
        self.assertEqual(order["shipping_cents"], 500)
        self.assertEqual(order["total_cents"], 2500)
        self.assertEqual(order["refunded_cents"], 0)
        self.assertTrue(order["order_id"].startswith("ord_"))
        self.assertEqual(len(order["lines"]), 1)
        self.assertEqual(order["lines"][0]["sku"], "A1")
        self.assertEqual(order["lines"][0]["subtotal_cents"], 2000)

    def test_deterministic_order_id(self):
        items = [{"sku": "SKU1", "unit_price_cents": 500, "quantity": 1}]
        order1 = create_order(items, coupon={"type": "percent", "value": 10}, tax_rate_bps=100, shipping_cents=200)
        order2 = create_order(items, coupon={"type": "percent", "value": 10}, tax_rate_bps=100, shipping_cents=200)
        self.assertEqual(order1["order_id"], order2["order_id"])

    def test_discount_allocation_and_ties(self):
        # Two items with equal subtotal 1000 each, discount 15% -> 300 total discount.
        # 150 each. Test tie-breaking by ascending SKU.
        items = [
            {"sku": "B_sku", "unit_price_cents": 1000, "quantity": 1},
            {"sku": "A_sku", "unit_price_cents": 1000, "quantity": 1},
        ]
        order = create_order(items, coupon={"type": "percent", "value": 15})
        self.assertEqual(order["discount_cents"], 300)
        # Lines should be sorted by input order or checked per SKU
        lines_by_sku = {l["sku"]: l for l in order["lines"]}
        self.assertEqual(lines_by_sku["A_sku"]["discount_cents"], 150)
        self.assertEqual(lines_by_sku["B_sku"]["discount_cents"], 150)

    def test_fixed_discount_cap(self):
        items = [{"sku": "X1", "unit_price_cents": 500, "quantity": 1}]
        order = create_order(items, coupon={"type": "fixed", "value": 1000})
        self.assertEqual(order["discount_cents"], 500)  # Capped at subtotal

    def test_per_line_tax(self):
        items = [
            {"sku": "L1", "unit_price_cents": 100, "quantity": 1},
            {"sku": "L2", "unit_price_cents": 33, "quantity": 1},
        ]
        # tax_rate_bps = 1000 (10%)
        # Net L1 = 100 -> tax = 10
        # Net L2 = 33 -> tax = 3 (floor(33 * 1000 / 10000) = floor(3.3) = 3)
        order = create_order(items, tax_rate_bps=1000)
        self.assertEqual(order["lines"][0]["tax_cents"], 10)
        self.assertEqual(order["lines"][1]["tax_cents"], 3)
        self.assertEqual(order["tax_cents"], 13)

    def test_invalid_inputs(self):
        valid_item = {"sku": "A", "unit_price_cents": 100, "quantity": 1}
        
        # Invalid items
        with self.assertRaises(ValueError):
            create_order("not a list")
        with self.assertRaises(ValueError):
            create_order([])
        with self.assertRaises(ValueError):
            create_order([{"sku": "", "unit_price_cents": 100, "quantity": 1}])
        with self.assertRaises(ValueError):
            create_order([{"sku": True, "unit_price_cents": 100, "quantity": 1}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": -10, "quantity": 1}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": True, "quantity": 1}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": 0}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": -1}])
        with self.assertRaises(ValueError):
            create_order([{"sku": "A", "unit_price_cents": 100, "quantity": True}])
        with self.assertRaises(ValueError):
            create_order([valid_item, valid_item])  # Duplicate SKUs

        # Invalid coupon
        with self.assertRaises(ValueError):
            create_order([valid_item], coupon="invalid")
        with self.assertRaises(ValueError):
            create_order([valid_item], coupon={"type": "invalid", "value": 10})
        with self.assertRaises(ValueError):
            create_order([valid_item], coupon={"type": "percent", "value": 101})
        with self.assertRaises(ValueError):
            create_order([valid_item], coupon={"type": "percent", "value": True})

        # Invalid tax & shipping
        with self.assertRaises(ValueError):
            create_order([valid_item], tax_rate_bps=10001)
        with self.assertRaises(ValueError):
            create_order([valid_item], tax_rate_bps=True)
        with self.assertRaises(ValueError):
            create_order([valid_item], shipping_cents=-1)
        with self.assertRaises(ValueError):
            create_order([valid_item], shipping_cents=True)
        with self.assertRaises(ValueError):
            create_order([valid_item], order_id=True)
        with self.assertRaises(ValueError):
            create_order([valid_item], order_id="")

    def test_state_machine_transitions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        self.assertEqual(order["status"], "draft")
        
        # draft -> submitted
        order = transition_order(order, "submit")
        self.assertEqual(order["status"], "submitted")
        self.assertEqual(order["version"], 2)
        
        # submitted -> paid
        order = transition_order(order, "pay")
        self.assertEqual(order["status"], "paid")
        self.assertEqual(order["version"], 3)
        
        # paid -> shipped
        order = transition_order(order, "ship")
        self.assertEqual(order["status"], "shipped")
        self.assertEqual(order["version"], 4)
        
        # Invalid transitions
        order2 = create_order(items)
        with self.assertRaises(ValueError):
            transition_order(order2, "pay")  # draft -> pay invalid
        with self.assertRaises(ValueError):
            transition_order(order2, "ship") # draft -> ship invalid
            
        # Cancelled transitions
        order3 = create_order(items)
        order3 = transition_order(order3, "cancel")
        self.assertEqual(order3["status"], "cancelled")
        with self.assertRaises(ValueError):
            transition_order(order3, "submit")

        # expected_version check
        order4 = create_order(items)
        with self.assertRaises(ValueError):
            transition_order(order4, "submit", expected_version=2)
        with self.assertRaises(ValueError):
            transition_order(order4, "submit", expected_version=True)

    def test_refunds(self):
        items = [{"sku": "A", "unit_price_cents": 250, "quantity": 4}]
        order = create_order(items)
        
        # Cannot refund draft order
        with self.assertRaises(ValueError):
            refund_order(order)
            
        order = transition_order(order, "submit")
        order = transition_order(order, "pay")
        
        # Partial refund (2 of 4) -> 2 * 250 = 500 cents
        order = refund_order(order, quantities={"A": 2})
        self.assertEqual(order["refunded_cents"], 500)
        self.assertEqual(order["version"], 4)
        
        # Second partial refund (1 of 4) -> cumulative 3 -> incremental 250, total 750
        order = refund_order(order, quantities={"A": 1})
        self.assertEqual(order["refunded_cents"], 750)
        
        # Refund remaining (1 of 4) via quantities=None -> total 1000
        order = refund_order(order, quantities=None)
        self.assertEqual(order["refunded_cents"], 1000)
        
        # Refund exceeding remaining
        with self.assertRaises(ValueError):
            refund_order(order, quantities={"A": 1})

        # Zero refund or unknown SKU
        order_fresh = create_order(items)
        order_fresh = transition_order(order_fresh, "submit")
        order_fresh = transition_order(order_fresh, "pay")
        with self.assertRaises(ValueError):
            refund_order(order_fresh, quantities={"UNKNOWN": 1})
        with self.assertRaises(ValueError):
            refund_order(order_fresh, quantities={})

    def test_no_input_mutation(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        items_copy = copy.deepcopy(items)
        order = create_order(items, coupon={"type": "percent", "value": 10})
        self.assertEqual(items, items_copy)
        
        order_copy = copy.deepcopy(order)
        order_trans = transition_order(order, "submit")
        self.assertEqual(order, order_copy)

    def test_incremental_refund_history_and_zero_value(self):
        items = [{"sku": "FREE", "unit_price_cents": 0, "quantity": 2}]
        order = create_order(items)
        order = transition_order(order, "submit")
        order = transition_order(order, "pay")
        
        order = refund_order(order, quantities={"FREE": 1})
        self.assertEqual(order["refunded_cents"], 0)
        self.assertEqual(order["history"][-1]["quantities"], {"FREE": 1})
        self.assertEqual(order["history"][-1]["amount_cents"], 0)
        
        order = refund_order(order, quantities={"FREE": 1})
        self.assertEqual(order["history"][-1]["quantities"], {"FREE": 1})

    def test_malformed_history_rejection(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        bad = copy.deepcopy(order)
        bad["history"][-1]["version"] = 999
        with self.assertRaises(ValueError):
            transition_order(bad, "submit")


if __name__ == "__main__":
    unittest.main()
