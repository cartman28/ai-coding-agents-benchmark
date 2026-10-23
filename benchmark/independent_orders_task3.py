"""Independent acceptance tests for Issue #9; do not modify candidate branches."""
import copy
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
from pricebot.orders import create_order, transition_order, refund_order
from pricebot.storage import save_order, load_order
from pricebot.pricing import discounted_price


def reference(items, coupon=None, tax_rate_bps=0, shipping_cents=0):
    clean = [{k: x[k] for k in ("sku", "unit_price_cents", "quantity")} for x in items]
    subs = [x["unit_price_cents"] * x["quantity"] for x in clean]
    subtotal = sum(subs)
    discount = 0 if coupon is None else (subtotal * coupon["value"] // 100 if coupon["type"] == "percent" else min(subtotal, coupon["value"]))
    shares = [0] * len(clean)
    if subtotal:
        numerators = [discount * s for s in subs]
        shares = [v // subtotal for v in numerators]
        remainder = discount - sum(shares)
        for i in sorted(range(len(clean)), key=lambda i: (-(numerators[i] % subtotal), clean[i]["sku"]))[:remainder]:
            shares[i] += 1
    lines = []
    for x, sub, disc in zip(clean, subs, shares):
        net = sub - disc
        tax = net * tax_rate_bps // 10000
        lines.append(dict(sku=x["sku"], subtotal_cents=sub, discount_cents=disc,
                          net_cents=net, tax_cents=tax, total_cents=net + tax, quantity=x["quantity"]))
    canonical = dict(items=clean, coupon=coupon, tax_rate_bps=tax_rate_bps, shipping_cents=shipping_cents)
    oid = "ord_" + hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
    return dict(order_id=oid, version=1, status="draft", items=clean, lines=lines,
                subtotal_cents=subtotal, discount_cents=discount,
                tax_cents=sum(x["tax_cents"] for x in lines), shipping_cents=shipping_cents,
                total_cents=sum(x["total_cents"] for x in lines) + shipping_cents,
                refunded_cents=0, history=[dict(version=1, event="created", status="draft")])


def paid(items, **kw):
    o = create_order(items, **kw)
    o = transition_order(o, "submit")
    return transition_order(o, "pay")


class Acceptance(unittest.TestCase):
    def test_pricing_regression(self):
        self.assertEqual(discounted_price(101, 50), 50)
        self.assertEqual(discounted_price(1, 50), 0)

    def test_reference_300_random_orders(self):
        rng = random.Random(20261023)
        for i in range(300):
            items = [dict(sku="SKU%02d" % j, unit_price_cents=rng.randrange(100000), quantity=rng.randrange(1, 15), extra="ignored") for j in range(rng.randrange(1, 10))]
            rng.shuffle(items)
            coupon = rng.choice([None, dict(type="percent", value=rng.randrange(101)), dict(type="fixed", value=rng.randrange(1000000))])
            kwargs = dict(coupon=coupon, tax_rate_bps=rng.randrange(10001), shipping_cents=rng.randrange(10000))
            original = copy.deepcopy((items, kwargs))
            with self.subTest(case=i):
                self.assertEqual(create_order(items, **kwargs), reference(items, **kwargs))
                self.assertEqual((items, kwargs), original)

    def test_custom_order_id_lifecycle(self):
        items = [dict(sku="A", unit_price_cents=100, quantity=2)]
        o = create_order(items, order_id="customer-123")
        self.assertEqual(o["order_id"], "customer-123")
        o = transition_order(o, "submit")
        o = transition_order(o, "pay")
        o = refund_order(o, quantities={"A": 1})
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "order.json"
            save_order(p, o)
            self.assertEqual(load_order(p), o)

    def test_partial_refund_history_and_batch_invariance(self):
        items = [dict(sku="A", unit_price_cents=101, quantity=4)]
        o = paid(items, tax_rate_bps=1234, shipping_cents=77)
        once = refund_order(o, quantities={"A": 4})
        many = o
        for _ in range(4):
            many = refund_order(many, quantities={"A": 1})
        self.assertEqual(many["refunded_cents"], once["refunded_cents"])
        self.assertEqual(many["refunded_cents"], o["lines"][0]["total_cents"])
        self.assertEqual([h["quantities"] for h in many["history"] if h["event"] == "refund"], [{"A": 1}] * 4)
        with self.assertRaises(ValueError):
            refund_order(many, quantities={"A": 1})

    def test_refund_batches_multiple_skus(self):
        items = [dict(sku="A", unit_price_cents=11, quantity=3), dict(sku="B", unit_price_cents=23, quantity=2)]
        o = paid(items, coupon={"type": "fixed", "value": 7}, tax_rate_bps=777)
        a = refund_order(o, quantities={"A": 1, "B": 1})
        b = refund_order(a, quantities={"A": 2})
        c = refund_order(b, quantities={"B": 1})
        self.assertEqual(c["refunded_cents"], sum(x["total_cents"] for x in o["lines"]))
        self.assertEqual([h["quantities"] for h in c["history"] if h["event"] == "refund"],
                         [{"A": 1, "B": 1}, {"A": 2}, {"B": 1}])

    def test_zero_value_refund_is_valid(self):
        o = paid([dict(sku="FREE", unit_price_cents=0, quantity=2)], shipping_cents=50)
        a = refund_order(o, quantities={"FREE": 1})
        self.assertEqual(a["refunded_cents"], 0)
        self.assertEqual(a["history"][-1]["amount_cents"], 0)
        b = refund_order(a, quantities={"FREE": 1})
        self.assertEqual(b["version"], a["version"] + 1)
        self.assertEqual(b["refunded_cents"], 0)
        with self.assertRaises(ValueError):
            refund_order(b)

    def test_subcent_refund_is_valid(self):
        o = paid([dict(sku="A", unit_price_cents=1, quantity=3)])
        first = refund_order(o, quantities={"A": 1})
        self.assertEqual(first["refunded_cents"], 0)
        self.assertEqual(refund_order(first, quantities={"A": 2})["refunded_cents"], 3)

    def test_state_machine_and_immutability(self):
        o = create_order([dict(sku="X", unit_price_cents=7, quantity=1)])
        old = copy.deepcopy(o)
        s = transition_order(o, "submit", expected_version=1)
        self.assertEqual(o, old)
        self.assertEqual((s["status"], s["version"]), ("submitted", 2))
        with self.assertRaises(ValueError):
            transition_order(s, "ship")
        with self.assertRaises(ValueError):
            transition_order(s, "pay", expected_version=1)
        self.assertEqual(transition_order(s, "cancel")["status"], "cancelled")
        with self.assertRaises(ValueError):
            refund_order(s)

    def test_validation_rejects_bools_and_duplicates(self):
        good = [dict(sku="A", unit_price_cents=100, quantity=1)]
        invalid = [[dict(sku="A", unit_price_cents=True, quantity=1)],
                   [dict(sku="A", unit_price_cents=1, quantity=False)], good + copy.deepcopy(good)]
        for items in invalid:
            with self.subTest(items=items), self.assertRaises(ValueError):
                create_order(items)
        for kw in [{"shipping_cents": True}, {"tax_rate_bps": True}, {"coupon": {"type": "fixed", "value": True}}]:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                create_order(good, **kw)

    def test_storage_roundtrip_and_versions(self):
        o = create_order([dict(sku="A", unit_price_cents=25, quantity=1)])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "order.json"
            save_order(p, o, expected_version=0)
            self.assertEqual(load_order(p), o)
            with self.assertRaises(ValueError):
                save_order(p, o, expected_version=0)
            new = transition_order(o, "submit")
            save_order(p, new, expected_version=1)
            self.assertEqual(load_order(p), new)
            with self.assertRaises(ValueError):
                save_order(p, o, expected_version=1)
            self.assertEqual(load_order(p), new)

    def test_storage_missing_and_corrupt(self):
        o = create_order([dict(sku="A", unit_price_cents=1, quantity=1)])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "missing" / "order.json"
            with self.assertRaises(FileNotFoundError):
                save_order(p, o)
            with self.assertRaises(FileNotFoundError):
                load_order(p)
            p = Path(d) / "order.json"
            p.write_text("{broken", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_order(p)

    def test_atomic_replace_failure_preserves_old_file(self):
        o = create_order([dict(sku="A", unit_price_cents=1, quantity=1)])
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "order.json"
            save_order(p, o)
            before = p.read_bytes()
            with patch("os.replace", side_effect=OSError("injected failure")):
                with self.assertRaises(OSError):
                    save_order(p, transition_order(o, "submit"))
            self.assertEqual(p.read_bytes(), before)
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()), ["order.json"])

    def test_relative_storage_temp_same_directory(self):
        o = create_order([dict(sku="A", unit_price_cents=2, quantity=1)])
        with tempfile.TemporaryDirectory() as d:
            cwd = os.getcwd()
            try:
                os.chdir(d)
                save_order("order.json", o)
                self.assertEqual(load_order("order.json"), o)
                self.assertEqual(sorted(os.listdir(".")), ["order.json"])
            finally:
                os.chdir(cwd)

    def test_malformed_history_rejected(self):
        o = create_order([dict(sku="A", unit_price_cents=10, quantity=1)])
        bad = copy.deepcopy(o)
        bad["history"][-1]["version"] = 999
        with self.assertRaises(ValueError):
            transition_order(bad, "submit")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "order.json"
            with self.assertRaises(ValueError):
                save_order(p, bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
