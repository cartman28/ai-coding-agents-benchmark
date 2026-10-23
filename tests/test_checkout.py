import unittest
from pricebot.checkout import checkout


class CheckoutTests(unittest.TestCase):
    def test_basic_percentage_coupon(self):
        items = [
            {"sku": "A1", "unit_price_cents": 1000, "quantity": 2},
            {"sku": "B2", "unit_price_cents": 500, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 10}, tax_rate_bps=1000, shipping_cents=500)
        self.assertEqual(res["subtotal_cents"], 2500)
        self.assertEqual(res["discount_cents"], 250)
        self.assertEqual(res["tax_cents"], 225)
        self.assertEqual(res["shipping_cents"], 500)
        self.assertEqual(res["total_cents"], 2500 - 250 + 225 + 500)
        self.assertEqual(len(res["lines"]), 2)
        self.assertEqual(res["lines"][0]["sku"], "A1")

    def test_basic_fixed_coupon(self):
        items = [
            {"sku": "A1", "unit_price_cents": 1000, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "fixed", "value": 200}, tax_rate_bps=0, shipping_cents=0)
        self.assertEqual(res["subtotal_cents"], 1000)
        self.assertEqual(res["discount_cents"], 200)
        self.assertEqual(res["lines"][0]["net_cents"], 800)
        self.assertEqual(res["total_cents"], 800)

    def test_fixed_coupon_exceeding_subtotal(self):
        items = [
            {"sku": "A1", "unit_price_cents": 100, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "fixed", "value": 500})
        self.assertEqual(res["subtotal_cents"], 100)
        self.assertEqual(res["discount_cents"], 100)
        self.assertEqual(res["total_cents"], 0)

    def test_zero_price_items(self):
        items = [
            {"sku": "FREE", "unit_price_cents": 0, "quantity": 5},
            {"sku": "PAID", "unit_price_cents": 1000, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 20})
        self.assertEqual(res["subtotal_cents"], 1000)
        self.assertEqual(res["discount_cents"], 200)
        self.assertEqual(res["lines"][0]["subtotal_cents"], 0)
        self.assertEqual(res["lines"][0]["discount_cents"], 0)

    def test_zero_subtotal(self):
        items = [
            {"sku": "FREE1", "unit_price_cents": 0, "quantity": 2},
            {"sku": "FREE2", "unit_price_cents": 0, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 50}, shipping_cents=100)
        self.assertEqual(res["subtotal_cents"], 0)
        self.assertEqual(res["discount_cents"], 0)
        self.assertEqual(res["total_cents"], 100)

    def test_tie_breaking_allocation_by_sku(self):
        items = [
            {"sku": "B", "unit_price_cents": 1, "quantity": 1},
            {"sku": "A", "unit_price_cents": 1, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "fixed", "value": 1})
        self.assertEqual(res["subtotal_cents"], 2)
        self.assertEqual(res["discount_cents"], 1)
        self.assertEqual(res["lines"][0]["sku"], "B")
        self.assertEqual(res["lines"][0]["discount_cents"], 0)
        self.assertEqual(res["lines"][1]["sku"], "A")
        self.assertEqual(res["lines"][1]["discount_cents"], 1)

    def test_input_order_preservation(self):
        items = [
            {"sku": "Z", "unit_price_cents": 100, "quantity": 1},
            {"sku": "M", "unit_price_cents": 200, "quantity": 1},
            {"sku": "A", "unit_price_cents": 300, "quantity": 1},
        ]
        res = checkout(items)
        self.assertEqual([l["sku"] for l in res["lines"]], ["Z", "M", "A"])

    def test_per_line_tax_rounding_vs_aggregate(self):
        items = [
            {"sku": "X1", "unit_price_cents": 15, "quantity": 1},
            {"sku": "X2", "unit_price_cents": 15, "quantity": 1},
        ]
        res = checkout(items, tax_rate_bps=1000)
        self.assertEqual(res["tax_cents"], 2)

    def test_invalid_inputs_bools_and_types(self):
        bad_cases = [
            "not a list",
            True,
            [],
            [True],
            ["not a dict"],
            [{"sku": "A", "unit_price_cents": 100}],
            [{"sku": "", "unit_price_cents": 100, "quantity": 1}],
            [{"sku": True, "unit_price_cents": 100, "quantity": 1}],
            [{"sku": 123, "unit_price_cents": 100, "quantity": 1}],
            [{"sku": "A", "unit_price_cents": -1, "quantity": 1}],
            [{"sku": "A", "unit_price_cents": True, "quantity": 1}],
            [{"sku": "A", "unit_price_cents": "100", "quantity": 1}],
            [{"sku": "A", "unit_price_cents": 100, "quantity": 0}],
            [{"sku": "A", "unit_price_cents": 100, "quantity": -1}],
            [{"sku": "A", "unit_price_cents": 100, "quantity": True}],
            [{"sku": "A", "unit_price_cents": 100, "quantity": "1"}],
        ]
        for case in bad_cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    checkout(case if isinstance(case, list) else case)

    def test_invalid_duplicate_sku(self):
        items = [
            {"sku": "A", "unit_price_cents": 100, "quantity": 1},
            {"sku": "A", "unit_price_cents": 200, "quantity": 1},
        ]
        with self.assertRaises(ValueError):
            checkout(items)

    def test_invalid_coupon_and_tax_shipping(self):
        item = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        bad_coupons = [
            True,
            "percent",
            {"type": "unknown", "value": 10},
            {"type": "percent", "value": 101},
            {"type": "percent", "value": -1},
            {"type": "percent", "value": True},
            {"type": "fixed", "value": -1},
            {"type": "fixed", "value": True},
            {"missing": "keys"}
        ]
        for c in bad_coupons:
            with self.subTest(coupon=c):
                with self.assertRaises(ValueError):
                    checkout(item, coupon=c)

        with self.assertRaises(ValueError):
            checkout(item, tax_rate_bps=10001)
        with self.assertRaises(ValueError):
            checkout(item, tax_rate_bps=-1)
        with self.assertRaises(ValueError):
            checkout(item, tax_rate_bps=True)

        with self.assertRaises(ValueError):
            checkout(item, shipping_cents=-1)
        with self.assertRaises(ValueError):
            checkout(item, shipping_cents=True)

    def test_non_mutation(self):
        items = [
            {"sku": "A", "unit_price_cents": 100, "quantity": 2, "extra": "info"}
        ]
        coupon = {"type": "fixed", "value": 50}
        items_copy = [dict(items[0])]
        coupon_copy = dict(coupon)

        checkout(items, coupon=coupon, tax_rate_bps=100, shipping_cents=200)

        self.assertEqual(items, items_copy)
        self.assertEqual(coupon, coupon_copy)

    def test_accounting_invariants(self):
        items = [
            {"sku": "SKU1", "unit_price_cents": 1234, "quantity": 3},
            {"sku": "SKU2", "unit_price_cents": 567, "quantity": 2},
            {"sku": "SKU3", "unit_price_cents": 89, "quantity": 5},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 15}, tax_rate_bps=825, shipping_cents=750)

        self.assertEqual(res["subtotal_cents"], sum(l["subtotal_cents"] for l in res["lines"]))
        self.assertEqual(res["discount_cents"], sum(l["discount_cents"] for l in res["lines"]))
        self.assertEqual(res["tax_cents"], sum(l["tax_cents"] for l in res["lines"]))

        for l in res["lines"]:
            self.assertEqual(l["net_cents"], l["subtotal_cents"] - l["discount_cents"])

        expected_total = res["subtotal_cents"] - res["discount_cents"] + res["tax_cents"] + res["shipping_cents"]
        self.assertEqual(res["total_cents"], expected_total)

    def test_extra_keys_ignored(self):
        items = [
            {"sku": "A", "unit_price_cents": 100, "quantity": 1, "description": "Test item", "category": "widgets"}
        ]
        res = checkout(items)
        self.assertEqual(res["subtotal_cents"], 100)


if __name__ == "__main__":
    unittest.main()
