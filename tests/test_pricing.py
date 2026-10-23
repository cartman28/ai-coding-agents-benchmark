import unittest

from pricebot.pricing import discounted_price


class PricingTests(unittest.TestCase):
    def test_standard_price(self):
        self.assertEqual(discounted_price(1000, 20), 800)

    def test_no_discount(self):
        self.assertEqual(discounted_price(1000, 0), 1000)

    def test_full_discount(self):
        self.assertEqual(discounted_price(1000, 100), 0)

    def test_acceptance_examples(self):
        examples = [
            (1000, 20, 800),
            (199, 25, 149),
            (99, 10, 89),
            (1, 50, 0),
            (1000, 0, 1000),
            (1000, 100, 0),
        ]
        for price, percent, expected in examples:
            with self.subTest(price=price, percent=percent):
                self.assertEqual(discounted_price(price, percent), expected)

    def test_invalid_inputs(self):
        for price, percent in [(-1, 10), (100, -1), (100, 101), (True, 10), (100, True), (100.0, 10), (100, 10.0)]:
            with self.subTest(price=price, percent=percent):
                with self.assertRaises(ValueError):
                    discounted_price(price, percent)


if __name__ == "__main__":
    unittest.main()
