"""Independent acceptance suite for benchmark issue #5.

Run against each candidate branch, never import candidates' own tests.
"""
import copy
import importlib.util
import pathlib
import random
import unittest

MODULE = pathlib.Path("pricebot/checkout.py")
spec = importlib.util.spec_from_file_location("candidate_checkout", MODULE)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
checkout = candidate.checkout


def reference(items, coupon=None, tax_rate_bps=0, shipping_cents=0):
    subtotals = [x["unit_price_cents"] * x["quantity"] for x in items]
    subtotal = sum(subtotals)
    if coupon is None:
        discount = 0
    elif coupon["type"] == "percent":
        discount = subtotal * coupon["value"] // 100
    else:
        discount = min(subtotal, coupon["value"])
    shares = [0] * len(items)
    if subtotal:
        numerators = [discount * v for v in subtotals]
        shares = [v // subtotal for v in numerators]
        left = discount - sum(shares)
        order = sorted(range(len(items)), key=lambda i: (-(numerators[i] % subtotal), items[i]["sku"]))
        for i in order[:left]:
            shares[i] += 1
    lines = []
    for item, sub, disc in zip(items, subtotals, shares):
        net = sub - disc
        lines.append({"sku": item["sku"], "subtotal_cents": sub,
                      "discount_cents": disc, "net_cents": net,
                      "tax_cents": net * tax_rate_bps // 10000})
    tax = sum(x["tax_cents"] for x in lines)
    return {"lines": lines, "subtotal_cents": subtotal, "discount_cents": discount,
            "tax_cents": tax, "shipping_cents": shipping_cents,
            "total_cents": subtotal - discount + tax + shipping_cents}


class IndependentAcceptance(unittest.TestCase):
    def assert_case(self, items, **kwargs):
        before = copy.deepcopy((items, kwargs))
        actual = checkout(items, **kwargs)
        self.assertEqual(actual, reference(items, **kwargs))
        self.assertEqual((items, kwargs), before)
        self.assertEqual(set(actual), {"lines", "subtotal_cents", "discount_cents",
                                        "tax_cents", "shipping_cents", "total_cents"})
        for line in actual["lines"]:
            self.assertEqual(set(line), {"sku", "subtotal_cents", "discount_cents",
                                          "net_cents", "tax_cents"})

    def test_all_discount_percentages(self):
        items = [{"sku": "Z", "unit_price_cents": 1, "quantity": 1},
                 {"sku": "A", "unit_price_cents": 2, "quantity": 1},
                 {"sku": "M", "unit_price_cents": 7, "quantity": 1}]
        for pct in range(101):
            with self.subTest(pct=pct):
                self.assert_case(items, coupon={"type": "percent", "value": pct}, tax_rate_bps=9999)

    def test_fixed_discount_every_cent(self):
        items = [{"sku": "Z", "unit_price_cents": 3, "quantity": 1},
                 {"sku": "A", "unit_price_cents": 3, "quantity": 1},
                 {"sku": "B", "unit_price_cents": 4, "quantity": 1}]
        for value in range(20):
            with self.subTest(value=value):
                self.assert_case(items, coupon={"type": "fixed", "value": value}, shipping_cents=19)

    def test_zero_subtotal_and_shipping(self):
        self.assert_case([{"sku": "free", "unit_price_cents": 0, "quantity": 10}],
                         coupon={"type": "fixed", "value": 999}, tax_rate_bps=10000, shipping_cents=42)

    def test_large_integers(self):
        self.assert_case([{"sku": "B", "unit_price_cents": 10**30, "quantity": 10**6},
                          {"sku": "A", "unit_price_cents": 10**30 + 1, "quantity": 3}],
                         coupon={"type": "percent", "value": 33}, tax_rate_bps=1234)

    def test_randomized_reference_1000_cases(self):
        rng = random.Random(20261023)
        for case in range(1000):
            n = rng.randint(1, 12)
            items = [{"sku": f"SKU-{i:02d}", "unit_price_cents": rng.randint(0, 100000),
                      "quantity": rng.randint(1, 20), "ignored": [1, 2]} for i in range(n)]
            rng.shuffle(items)
            kind = rng.choice(["none", "fixed", "percent"])
            coupon = None if kind == "none" else {
                "type": kind, "value": rng.randint(0, 100) if kind == "percent" else rng.randint(0, 2000000)}
            with self.subTest(case=case):
                self.assert_case(items, coupon=coupon, tax_rate_bps=rng.randint(0, 10000),
                                 shipping_cents=rng.randint(0, 100000))

    def test_invalid_inputs(self):
        valid = [{"sku": "X", "unit_price_cents": 100, "quantity": 1}]
        bad_items = [None, {}, (), "abc", [], [1], [None], [{}],
                     [{"sku": "", "unit_price_cents": 1, "quantity": 1}],
                     [{"sku": 1, "unit_price_cents": 1, "quantity": 1}],
                     [{"sku": "X", "unit_price_cents": True, "quantity": 1}],
                     [{"sku": "X", "unit_price_cents": -1, "quantity": 1}],
                     [{"sku": "X", "unit_price_cents": 1.0, "quantity": 1}],
                     [{"sku": "X", "unit_price_cents": 1, "quantity": False}],
                     [{"sku": "X", "unit_price_cents": 1, "quantity": 0}],
                     valid + copy.deepcopy(valid)]
        for x in bad_items:
            with self.subTest(items=x), self.assertRaises(ValueError):
                checkout(x)
        for kwargs in [
            {"coupon": {}}, {"coupon": "wrong"}, {"coupon": {"type": "percent", "value": True}},
            {"coupon": {"type": "percent", "value": 101}},
            {"coupon": {"type": "fixed", "value": -1}},
            {"coupon": {"type": "unknown", "value": 1}},
            {"tax_rate_bps": True}, {"tax_rate_bps": -1}, {"tax_rate_bps": 10001},
            {"tax_rate_bps": 0.5}, {"shipping_cents": True}, {"shipping_cents": -1},
            {"shipping_cents": 1.5},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                checkout(valid, **kwargs)


if __name__ == "__main__":
    unittest.main()
