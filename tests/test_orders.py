import unittest
import copy
from pricebot.orders import create_order, transition_order, refund_order


class OrderTests(unittest.TestCase):
    def test_create_order_basic(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 2}]
        order = create_order(items, coupon={"type": "percent", "value": 10}, tax_rate_bps=500, shipping_cents=200)
        self.assertEqual(order["status"], "draft")
        self.assertEqual(order["version"], 1)
        self.assertEqual(order["subtotal_cents"], 200)
        self.assertEqual(order["discount_cents"], 20)
        self.assertEqual(order["tax_cents"], 9)  # net 180 * 500 // 10000 = 9
        self.assertEqual(order["shipping_cents"], 200)
        self.assertEqual(order["total_cents"], 180 + 9 + 200) # 389
        self.assertEqual(order["refunded_cents"], 0)
        self.assertTrue(order["order_id"].startswith("ord_"))

    def test_discount_ties_largest_remainder(self):
        items = [
            {"sku": "Z", "unit_price_cents": 3, "quantity": 1},
            {"sku": "A", "unit_price_cents": 3, "quantity": 1},
            {"sku": "B", "unit_price_cents": 4, "quantity": 1},
        ]
        # subtotal 10. Discount 33% -> 3. Shares: item Z: 3*3/10=0.9(0, rem 9), A: 3*3/10=0.9(0, rem 9), B: 3*4/10=1.2(1, rem 2).
        # rems: Z:9, A:9, B:2. Total base=1. Need 3. Left=2.
        # Ties between Z and A (remainder 9) broken by ascending SKU: A before Z.
        # So A gets +1, Z gets +1. Total discounts: A:1, Z:1, B:1.
        order = create_order(items, coupon={"type": "percent", "value": 33})
        self.assertEqual(order["discount_cents"], 3)
        self.assertEqual(order["lines"][0]["sku"], "Z")
        self.assertEqual(order["lines"][0]["discount_cents"], 1)
        self.assertEqual(order["lines"][1]["sku"], "A")
        self.assertEqual(order["lines"][1]["discount_cents"], 1)
        self.assertEqual(order["lines"][2]["sku"], "B")
        self.assertEqual(order["lines"][2]["discount_cents"], 1)

    def test_per_line_tax_and_shipping(self):
        items = [
            {"sku": "X", "unit_price_cents": 100, "quantity": 1},
            {"sku": "Y", "unit_price_cents": 200, "quantity": 1},
        ]
        order = create_order(items, tax_rate_bps=1000, shipping_cents=500)
        # Line X: net 100, tax 10. Total 110.
        # Line Y: net 200, tax 20. Total 220.
        # Total tax = 30. Total = 300 + 30 + 500 = 830.
        self.assertEqual(order["tax_cents"], 30)
        self.assertEqual(order["total_cents"], 830)

    def test_deterministic_order_id(self):
        items1 = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        items2 = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o1 = create_order(items1, coupon={"type": "fixed", "value": 10}, tax_rate_bps=100, shipping_cents=50)
        o2 = create_order(items2, coupon={"type": "fixed", "value": 10}, tax_rate_bps=100, shipping_cents=50)
        self.assertEqual(o1["order_id"], o2["order_id"])

    def test_invalid_inputs_create_order(self):
        valid_items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        invalid_cases = [
            ([], {}),
            (valid_items, {"coupon": {"type": "percent", "value": 101}}),
            (valid_items, {"coupon": {"type": "fixed", "value": -1}}),
            (valid_items, {"coupon": "bad"}),
            (valid_items, {"tax_rate_bps": -1}),
            (valid_items, {"tax_rate_bps": 10001}),
            (valid_items, {"shipping_cents": -1}),
            ([{"sku": "", "unit_price_cents": 10, "quantity": 1}], {}),
            ([{"sku": "A", "unit_price_cents": -1, "quantity": 1}], {}),
            ([{"sku": "A", "unit_price_cents": 10, "quantity": 0}], {}),
            ([{"sku": "A", "unit_price_cents": 10, "quantity": 1}, {"sku": "A", "unit_price_cents": 20, "quantity": 1}], {}),
            (valid_items, {"tax_rate_bps": True}),
            (valid_items, {"shipping_cents": False}),
        ]
        for items, kwargs in invalid_cases:
            with self.subTest(items=items, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    create_order(items, **kwargs)

    def test_no_input_mutation(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        items_orig = copy.deepcopy(items)
        coupon = {"type": "percent", "value": 10}
        coupon_orig = copy.deepcopy(coupon)
        order = create_order(items, coupon=coupon)
        self.assertEqual(items, items_orig)
        self.assertEqual(coupon, coupon_orig)

        order_orig = copy.deepcopy(order)
        order2 = transition_order(order, "submit")
        self.assertEqual(order, order_orig)
        self.assertNotEqual(order2, order)

    def test_state_machine_transitions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o1 = create_order(items)
        self.assertEqual(o1["status"], "draft")
        self.assertEqual(o1["version"], 1)

        o2 = transition_order(o1, "submit")
        self.assertEqual(o2["status"], "submitted")
        self.assertEqual(o2["version"], 2)

        o3 = transition_order(o2, "pay")
        self.assertEqual(o3["status"], "paid")
        self.assertEqual(o3["version"], 3)

        o4 = transition_order(o3, "ship")
        self.assertEqual(o4["status"], "shipped")
        self.assertEqual(o4["version"], 4)

        # Cancel from draft
        o_cancel_draft = transition_order(o1, "cancel")
        self.assertEqual(o_cancel_draft["status"], "cancelled")

        # Cancel from submitted
        o_cancel_sub = transition_order(o2, "cancel")
        self.assertEqual(o_cancel_sub["status"], "cancelled")

    def test_invalid_transitions(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o1 = create_order(items)
        with self.assertRaises(ValueError):
            transition_order(o1, "pay")
        with self.assertRaises(ValueError):
            transition_order(o1, "ship")

        o2 = transition_order(o1, "submit")
        with self.assertRaises(ValueError):
            transition_order(o2, "ship")
        with self.assertRaises(ValueError):
            transition_order(o2, "submit")

    def test_expected_version_validation(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        o1 = create_order(items)
        with self.assertRaises(ValueError):
            transition_order(o1, "submit", expected_version=2)
        with self.assertRaises(ValueError):
            transition_order(o1, "submit", expected_version=True)
        o2 = transition_order(o1, "submit", expected_version=1)
        self.assertEqual(o2["version"], 2)

    def test_refund_full_and_partial(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 4}]
        o = create_order(items, tax_rate_bps=1000)
        o = transition_order(o, "submit")
        o = transition_order(o, "pay")
        o = transition_order(o, "ship")

        # Partial refund: 2 units
        o_ref1 = refund_order(o, quantities={"A": 2})
        self.assertEqual(o_ref1["version"], o["version"] + 1)
        self.assertEqual(o_ref1["refunded_cents"], 220)  # half of 440 total

        # Second partial refund: remaining 2 units
        o_ref2 = refund_order(o_ref1, quantities={"A": 2})
        self.assertEqual(o_ref2["refunded_cents"], 440)

        # Full refund with quantities=None
        o2 = create_order(items, tax_rate_bps=1000)
        o2 = transition_order(o2, "submit")
        o2 = transition_order(o2, "pay")
        o2_ref = refund_order(o2, quantities=None)
        self.assertEqual(o2_ref["refunded_cents"], 440)

    def test_refund_validation(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 2}]
        o = create_order(items)
        # Cannot refund draft order
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"A": 1})

        o = transition_order(o, "submit")
        o = transition_order(o, "pay")
        # Unknown SKU
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"B": 1})
        # Exceeding quantity
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"A": 3})
        # Zero quantity
        with self.assertRaises(ValueError):
            refund_order(o, quantities={"A": 0})


if __name__ == "__main__":
    unittest.main()
