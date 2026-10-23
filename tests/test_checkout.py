import copy
import unittest

from pricebot.checkout import checkout


class CheckoutTests(unittest.TestCase):
    def test_percentage_coupon(self):
        items = [{"sku": "A1", "unit_price_cents": 1000, "quantity": 2}]
        res = checkout(items, coupon={"type": "percent", "value": 10}, tax_rate_bps=1000, shipping_cents=500)
        self.assertEqual(res["subtotal_cents"], 2000)
        self.assertEqual(res["discount_cents"], 200)
        self.assertEqual(res["tax_cents"], 180)  # (1800 * 1000) // 10000 = 180
        self.assertEqual(res["shipping_cents"], 500)
        self.assertEqual(res["total_cents"], 2480)  # 1800 + 180 + 500 = 2480

    def test_fixed_coupon(self):
        items = [{"sku": "A1", "unit_price_cents": 1500, "quantity": 1}]
        res = checkout(items, coupon={"type": "fixed", "value": 300})
        self.assertEqual(res["subtotal_cents"], 1500)
        self.assertEqual(res["discount_cents"], 300)
        self.assertEqual(res["total_cents"], 1200)

    def test_fixed_coupon_exceeding_subtotal(self):
        items = [{"sku": "A1", "unit_price_cents": 200, "quantity": 1}]
        res = checkout(items, coupon={"type": "fixed", "value": 500})
        self.assertEqual(res["subtotal_cents"], 200)
        self.assertEqual(res["discount_cents"], 200)
        self.assertEqual(res["total_cents"], 0)
        self.assertEqual(res["lines"][0]["net_cents"], 0)

    def test_zero_price_items(self):
        items = [
            {"sku": "FREE1", "unit_price_cents": 0, "quantity": 2},
            {"sku": "PAID1", "unit_price_cents": 1000, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 20})
        self.assertEqual(res["subtotal_cents"], 1000)
        self.assertEqual(res["discount_cents"], 200)
        self.assertEqual(res["lines"][0]["discount_cents"], 0)
        self.assertEqual(res["lines"][1]["discount_cents"], 200)

    def test_zero_subtotal(self):
        items = [{"sku": "FREE1", "unit_price_cents": 0, "quantity": 5}]
        res = checkout(items, coupon={"type": "percent", "value": 50}, tax_rate_bps=500, shipping_cents=200)
        self.assertEqual(res["subtotal_cents"], 0)
        self.assertEqual(res["discount_cents"], 0)
        self.assertEqual(res["tax_cents"], 0)
        self.assertEqual(res["shipping_cents"], 200)
        self.assertEqual(res["total_cents"], 200)

    def test_tie_breaking_allocation_by_sku(self):
        # Subtotals: A: 100, B: 100. Total subtotal: 200. Discount: 1 cent.
        # Exact share: A: 0.5, B: 0.5. Remainders equal (100).
        # Tie break by ascending SKU: "A" comes before "B", so "A" gets the 1 cent discount.
        items = [
            {"sku": "B", "unit_price_cents": 100, "quantity": 1},
            {"sku": "A", "unit_price_cents": 100, "quantity": 1},
        ]
        res = checkout(items, coupon={"type": "fixed", "value": 1})
        lines_dict = {line["sku"]: line for line in res["lines"]}
        self.assertEqual(lines_dict["A"]["discount_cents"], 1)
        self.assertEqual(lines_dict["B"]["discount_cents"], 0)

    def test_input_order_preservation(self):
        items = [
            {"sku": "Z", "unit_price_cents": 500, "quantity": 1},
            {"sku": "M", "unit_price_cents": 300, "quantity": 1},
            {"sku": "A", "unit_price_cents": 200, "quantity": 1},
        ]
        res = checkout(items)
        skus = [line["sku"] for line in res["lines"]]
        self.assertEqual(skus, ["Z", "M", "A"])

    def test_per_line_tax_rounding_vs_aggregate(self):
        # Two items, each net 150 cents, tax rate 3333 bps (33.33%).
        # Per-line tax: (150 * 3333) // 10000 = 49 each. Total tax = 98.
        items = [
            {"sku": "X1", "unit_price_cents": 150, "quantity": 1},
            {"sku": "X2", "unit_price_cents": 150, "quantity": 1},
        ]
        res = checkout(items, tax_rate_bps=3333)
        self.assertEqual(res["tax_cents"], 49 + 49)

    def test_invalid_inputs_and_bools(self):
        valid_item = {"sku": "A", "unit_price_cents": 100, "quantity": 1}

        invalid_cases = [
            ("not a list", lambda: checkout("not-a-list")),
            ("empty list", lambda: checkout([])),
            ("item not dict", lambda: checkout(["not-a-dict"])),
            ("missing sku", lambda: checkout([{"unit_price_cents": 100, "quantity": 1}])),
            ("empty sku", lambda: checkout([{"sku": "", "unit_price_cents": 100, "quantity": 1}])),
            ("bool sku", lambda: checkout([{"sku": True, "unit_price_cents": 100, "quantity": 1}])),
            ("duplicate sku", lambda: checkout([{"sku": "A", "unit_price_cents": 100, "quantity": 1}, {"sku": "A", "unit_price_cents": 200, "quantity": 1}])),
            ("missing price", lambda: checkout([{"sku": "A", "quantity": 1}])),
            ("negative price", lambda: checkout([{"sku": "A", "unit_price_cents": -10, "quantity": 1}])),
            ("bool price", lambda: checkout([{"sku": "A", "unit_price_cents": True, "quantity": 1}])),
            ("missing quantity", lambda: checkout([{"sku": "A", "unit_price_cents": 100}])),
            ("zero quantity", lambda: checkout([{"sku": "A", "unit_price_cents": 100, "quantity": 0}])),
            ("negative quantity", lambda: checkout([{"sku": "A", "unit_price_cents": 100, "quantity": -1}])),
            ("bool quantity", lambda: checkout([{"sku": "A", "unit_price_cents": 100, "quantity": True}])),
            ("invalid coupon type", lambda: checkout([valid_item], coupon={"type": "invalid", "value": 10})),
            ("coupon value bool", lambda: checkout([valid_item], coupon={"type": "fixed", "value": True})),
            ("percent coupon > 100", lambda: checkout([valid_item], coupon={"type": "percent", "value": 101})),
            ("percent coupon < 0", lambda: checkout([valid_item], coupon={"type": "percent", "value": -1})),
            ("fixed coupon negative", lambda: checkout([valid_item], coupon={"type": "fixed", "value": -10})),
            ("coupon not dict", lambda: checkout([valid_item], coupon="not-a-dict")),
            ("coupon missing fields", lambda: checkout([valid_item], coupon={"type": "percent"})),
            ("tax rate negative", lambda: checkout([valid_item], tax_rate_bps=-1)),
            ("tax rate > 10000", lambda: checkout([valid_item], tax_rate_bps=10001)),
            ("tax rate bool", lambda: checkout([valid_item], tax_rate_bps=True)),
            ("shipping negative", lambda: checkout([valid_item], shipping_cents=-10)),
            ("shipping bool", lambda: checkout([valid_item], shipping_cents=True)),
        ]

        for desc, func in invalid_cases:
            with self.subTest(desc=desc):
                with self.assertRaises(ValueError):
                    func()

    def test_non_mutation(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 2, "extra": "info"}]
        items_copy = copy.deepcopy(items)
        coupon = {"type": "percent", "value": 10}
        coupon_copy = copy.deepcopy(coupon)

        checkout(items, coupon=coupon, tax_rate_bps=100, shipping_cents=50)

        self.assertEqual(items, items_copy)
        self.assertEqual(coupon, coupon_copy)

    def test_accounting_invariants(self):
        items = [
            {"sku": "SKU1", "unit_price_cents": 333, "quantity": 3},
            {"sku": "SKU2", "unit_price_cents": 777, "quantity": 2},
        ]
        res = checkout(items, coupon={"type": "percent", "value": 15}, tax_rate_bps=850, shipping_cents=350)

        # Sum of line subtotals == subtotal_cents
        self.assertEqual(sum(l["subtotal_cents"] for l in res["lines"]), res["subtotal_cents"])
        # Sum of line discounts == discount_cents
        self.assertEqual(sum(l["discount_cents"] for l in res["lines"]), res["discount_cents"])
        # Sum of line net_cents == subtotal_cents - discount_cents
        self.assertEqual(sum(l["net_cents"] for l in res["lines"]), res["subtotal_cents"] - res["discount_cents"])
        # Sum of line tax_cents == tax_cents
        self.assertEqual(sum(l["tax_cents"] for l in res["lines"]), res["tax_cents"])
        # total_cents == subtotal_cents - discount_cents + tax_cents + shipping_cents
        expected_total = res["subtotal_cents"] - res["discount_cents"] + res["tax_cents"] + res["shipping_cents"]
        self.assertEqual(res["total_cents"], expected_total)


if __name__ == "__main__":
    unittest.main()
