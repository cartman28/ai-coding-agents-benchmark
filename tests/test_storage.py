import unittest
import tempfile
import os
from pathlib import Path

from pricebot.orders import create_order, transition_order
from pricebot.storage import save_order, load_order


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "order.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load_order(self):
        items = [{"sku": "A", "unit_price_cents": 150, "quantity": 2}]
        order = create_order(items, shipping_cents=200)
        save_order(self.path, order)

        loaded = load_order(self.path)
        self.assertEqual(loaded["order_id"], order["order_id"])
        self.assertEqual(loaded["total_cents"], order["total_cents"])
        self.assertEqual(loaded["version"], 1)

        # Check raw JSON content formatting rules
        raw_text = self.path.read_text(encoding="utf-8")
        self.assertIn('":"', raw_text)
        self.assertNotIn(": ", raw_text)
        self.assertNotIn(", ", raw_text)

    def test_optimistic_concurrency_success(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        save_order(self.path, order)

        submitted = transition_order(order, "submit")
        save_order(self.path, submitted, expected_version=1)
        self.assertEqual(load_order(self.path)["version"], 2)

    def test_optimistic_concurrency_failure(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        save_order(self.path, order)

        submitted = transition_order(order, "submit")
        with self.assertRaises(ValueError):
            save_order(self.path, submitted, expected_version=5)

    def test_missing_file_load(self):
        with self.assertRaises(FileNotFoundError):
            load_order(self.path)

    def test_corrupt_json_load(self):
        self.path.write_text("invalid json {", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_order(self.path)

    def test_missing_parent_directory(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        non_existent_path = Path(self.temp_dir.name) / "nonexistent" / "order.json"
        with self.assertRaises((FileNotFoundError, OSError)):
            save_order(non_existent_path, order)

    def test_expected_version_missing_file(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        # expected_version=0 or None allowed when file missing
        save_order(self.path, order, expected_version=0)
        self.assertTrue(self.path.exists())

        path2 = Path(self.temp_dir.name) / "order2.json"
        with self.assertRaises(ValueError):
            save_order(path2, order, expected_version=1)

    def test_bool_expected_version(self):
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        with self.assertRaises(ValueError):
            save_order(self.path, order, expected_version=True)

    def test_malformed_order_save(self):
        with self.assertRaises(ValueError):
            save_order(self.path, {"invalid": "order"})


if __name__ == "__main__":
    unittest.main()
