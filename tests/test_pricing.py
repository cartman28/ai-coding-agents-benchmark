import unittest

from pricebot.pricing import discounted_price


class PricingTests(unittest.TestCase):
    def test_standard_price(self):
        self.assertEqual(discounted_price(1000, 20), 800)

    def test_no_discount(self):
        self.assertEqual(discounted_price(1000, 0), 1000)

    def test_full_discount(self):
        self.assertEqual(discounted_price(1000, 100), 0)

    def test_rounding_regression(self):
        self.assertEqual(discounted_price(150, 10), 135)
        self.assertEqual(discounted_price(101, 50), 50)

    def test_invalid_inputs(self):
        for price, percent in [(-1, 10), (100, -1), (100, 101), (True, 10), (100, True)]:
            with self.subTest(price=price, percent=percent):
                with self.assertRaises(ValueError):
                    discounted_price(price, percent)


if __name__ == "__main__":
    unittest.main()
