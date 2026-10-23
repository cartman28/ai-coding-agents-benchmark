import unittest
from pricebot.checkout import checkout


class CheckoutTests(unittest.TestCase):
    def test_basic_checkout(self):
        items = [
            {"sku": "A1", "unit_price_cents": 1000, "quantity": 2},
            {"sku": "B2", "unit_price_cents": 500, "quantity": 1},
        ]
        res = checkout(items, shipping_cents=500)
        self.assertEqual(res["subtotal_cents"], 2500)
        self.assertEqual(res["discount_cents"], 0)
        self.assertEqual(res["tax_cents"], 0)
        self.assertEqual(res["shipping_cents"], 500)
        self.assertEqual(res["total_cents"], 3000)
        self.assertEqual(len(res["lines"]), 2)
        self.assertEqual(res["lines"][0]["sku"], "A1")
        self.assertEqual(res["lines"][0]["subtotal_cents"], 2000)
        self.assertEqual(res["lines"][0]["net_cents"], 2000)

    def test_percent_coupon(self):
        items = [
            {"sku": "ITEM1", "unit_price_cents": 1000, "quantity": 1},
        ]
        coupon = {"type": "percent", "value": 15}
        res = checkout(items, coupon=coupon, tax_rate_bps=1000, shipping_cents=200)
        self.assertEqual(res["subtotal_cents"], 1000)
        self.assertEqual(res["discount_cents"], 150)
        self.assertEqual(res["tax_cents"], 85)
        self.assertEqual(res["total_cents"], 1135)

    def test_fixed_coupon(self):
        items = [
            {"sku": "X", "unit_price_cents": 2000, "quantity": 1},
        ]
        coupon = {"type": "fixed", "value": 500}
        res = checkout(items, coupon=coupon)
        self.assertEqual(res["discount_cents"], 500)
        self.assertEqual(res["total_cents"], 1500)

    def test_fixed_coupon_exceeding_subtotal(self):
        items = [
            {"sku": "X", "unit_price_cents": 300, "quantity": 1},
        ]
        coupon = {"type": "fixed", "value": 1000}
        res = checkout(items, coupon=coupon)
        self.assertEqual(res["subtotal_cents"], 300)
        self.assertEqual(res["discount_cents"], 300)
        self.assertEqual(res["total_cents"], 0)

    def test_zero_price_items(self):
        items = [
            {"sku": "FREE", "unit_price_cents": 0, "quantity": 5},
            {"sku": "PAID", "unit_price_cents": 100, "quantity": 2},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 50})
        self.assertEqual(res["subtotal_cents"], 200)
        self.assertEqual(res["discount_cents"], 100)
        self.assertEqual(res["total_cents"], 100)

    def test_zero_subtotal(self):
        items = [
            {"sku": "FREE1", "unit_price_cents": 0, "quantity": 2},
            {"sku": "FREE2", "unit_price_cents": 0, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 20}, shipping_cents=300)
        self.assertEqual(res["subtotal_cents"], 0)
        self.assertEqual(res["discount_cents"], 0)
        self.assertEqual(res["tax_cents"], 0)
        self.assertEqual(res["total_cents"], 300)
        for line in res["lines"]:
            self.assertEqual(line["discount_cents"], 0)
            self.assertEqual(line["net_cents"], 0)
            self.assertEqual(line["tax_cents"], 0)

    def test_tie_breaking_allocation_by_sku(self):
        items = [
            {"sku": "Z_ITEM", "unit_price_cents": 100, "quantity": 1},
            {"sku": "A_ITEM", "unit_price_cents": 100, "quantity": 1},
        ]
        coupon = {"type": "fixed", "value": 1}
        res = checkout(items, coupon=coupon)
        lines = res["lines"]
        self.assertEqual(lines[0]["sku"], "Z_ITEM")
        self.assertEqual(lines[0]["discount_cents"], 0)
        self.assertEqual(lines[1]["sku"], "A_ITEM")
        self.assertEqual(lines[1]["discount_cents"], 1)

    def test_input_order_preservation(self):
        items = [
            {"sku": "C", "unit_price_cents": 100, "quantity": 1},
            {"sku": "A", "unit_price_cents": 200, "quantity": 1},
            {"sku": "B", "unit_price_cents": 300, "quantity": 1},
        ]
        res = checkout(items)
        skus = [l["sku"] for l in res["lines"]]
        self.assertEqual(skus, ["C", "A", "B"])

    def test_per_line_tax_rounding_vs_aggregate(self):
        items = [
            {"sku": "L1", "unit_price_cents": 15, "quantity": 1},
            {"sku": "L2", "unit_price_cents": 15, "quantity": 1},
        ]
        res = checkout(items, tax_rate_bps=500)
        self.assertEqual(res["lines"][0]["tax_cents"], 0)
        self.assertEqual(res["lines"][1]["tax_cents"], 0)
        self.assertEqual(res["tax_cents"], 0)

    def test_invalid_inputs_booleans(self):
        invalid_cases = [
            ([{"sku": "A", "unit_price_cents": True, "quantity": 1}], {}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": False}], {}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"coupon": {"type": "percent", "value": True}}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"tax_rate_bps": True}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"shipping_cents": False}),
        ]
        for args, kwargs in invalid_cases:
            with self.subTest(args=args, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    checkout(args, **kwargs)

    def test_invalid_inputs_duplicate_sku(self):
        items = [
            {"sku": "DUP", "unit_price_cents": 100, "quantity": 1},
            {"sku": "DUP", "unit_price_cents": 200, "quantity": 1},
        ]
        with self.assertRaises(ValueError):
            checkout(items)

    def test_invalid_inputs_missing_fields_and_types(self):
        invalid_calls = [
            ("not-a-list", {}),
            ([], {}),
            ([{"unit_price_cents": 100, "quantity": 1}], {}),
            ([{"sku": "A", "quantity": 1}], {}),
            ([{"sku": "A", "unit_price_cents": 100}], {}),
            ([{"sku": 123, "unit_price_cents": 100, "quantity": 1}], {}),
            ([{"sku": "", "unit_price_cents": 100, "quantity": 1}], {}),
            ([{"sku": "A", "unit_price_cents": -1, "quantity": 1}], {}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 0}], {}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": -1}], {}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"coupon": {"type": "invalid", "value": 10}}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"coupon": {"type": "percent", "value": 150}}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"coupon": {"type": "fixed", "value": -10}}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"tax_rate_bps": 10001}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"tax_rate_bps": -1}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"shipping_cents": -1}),
            ([{"sku": "A", "unit_price_cents": 100, "quantity": 1}], {"coupon": "not-a-dict"}),
        ]
        for items, kwargs in invalid_calls:
            with self.subTest(items=items, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    checkout(items, **kwargs)

    def test_nonmutation(self):
        items = [
            {"sku": "A", "unit_price_cents": 100, "quantity": 2, "extra": "info"},
        ]
        coupon = {"type": "fixed", "value": 10}
        items_copy = [dict(items[0])]
        coupon_copy = dict(coupon)

        checkout(items, coupon=coupon, tax_rate_bps=100, shipping_cents=50)

        self.assertEqual(items, items_copy)
        self.assertEqual(coupon, coupon_copy)

    def test_accounting_invariants(self):
        items = [
            {"sku": "S1", "unit_price_cents": 1234, "quantity": 3},
            {"sku": "S2", "unit_price_cents": 5678, "quantity": 1},
            {"sku": "S3", "unit_price_cents": 90, "quantity": 10},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 12}, tax_rate_bps=850, shipping_cents=750)

        self.assertEqual(sum(l["subtotal_cents"] for l in res["lines"]), res["subtotal_cents"])
        self.assertEqual(sum(l["discount_cents"] for l in res["lines"]), res["discount_cents"])
        self.assertEqual(sum(l["net_cents"] for l in res["lines"]), res["subtotal_cents"] - res["discount_cents"])
        self.assertEqual(sum(l["tax_cents"] for l in res["lines"]), res["tax_cents"])

        expected_total = res["subtotal_cents"] - res["discount_cents"] + res["tax_cents"] + res["shipping_cents"]
        self.assertEqual(res["total_cents"], expected_total)


if __name__ == "__main__":
    unittest.main()
