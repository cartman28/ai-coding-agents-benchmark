import unittest
import pathlib
import tempfile
import json
from pricebot.orders import create_order, transition_order
from pricebot.storage import save_order, load_order


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self.tmp_dir.name)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_save_and_load_order(self):
        file_path = self.tmp_path / "order1.json"
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 2}]
        order = create_order(items, shipping_cents=100)
        save_order(file_path, order)

        loaded = load_order(file_path)
        self.assertEqual(order, loaded)
        # Ensure fresh dict
        self.assertIsNot(order, loaded)

    def test_optimistic_concurrency_success(self):
        file_path = self.tmp_path / "order2.json"
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order1 = create_order(items)
        save_order(file_path, order1, expected_version=0)  # file didn't exist, version 0 or None accepted

        order2 = transition_order(order1, "submit")
        save_order(file_path, order2, expected_version=1)

        loaded = load_order(file_path)
        self.assertEqual(loaded["version"], 2)

    def test_optimistic_concurrency_conflict(self):
        file_path = self.tmp_path / "order3.json"
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order1 = create_order(items)
        save_order(file_path, order1)

        order2 = transition_order(order1, "submit")
        # Stale expected_version=1 when stored version is now 2 (or if file exists and expected_version=1 vs loaded version 1... wait, order1 is version 1, file has version 1. If we try to save order1 with expected_version=2, it should conflict).
        order_stale = create_order(items)
        with self.assertRaises(ValueError):
            save_order(file_path, order_stale, expected_version=2)

    def test_optimistic_concurrency_missing_file_rules(self):
        file_path = self.tmp_path / "missing.json"
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        # expected_version=None or 0 accepted when missing
        save_order(file_path, order, expected_version=0)
        
        file_path.unlink()
        with self.assertRaises(ValueError):
            save_order(file_path, order, expected_version=1)

    def test_missing_file_load(self):
        file_path = self.tmp_path / "nonexistent.json"
        with self.assertRaises(FileNotFoundError):
            load_order(file_path)

    def test_corrupt_json_load(self):
        file_path = self.tmp_path / "corrupt.json"
        file_path.write_text("{invalid json", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_order(file_path)

    def test_malformed_order_load(self):
        file_path = self.tmp_path / "malformed.json"
        file_path.write_text(json.dumps({"order_id": "ord_123", "version": 1}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_order(file_path)

    def test_missing_parent_directory(self):
        file_path = self.tmp_path / "nonexistent_dir" / "order.json"
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        with self.assertRaises((FileNotFoundError, OSError)):
            save_order(file_path, order)

    def test_invalid_expected_version_type(self):
        file_path = self.tmp_path / "order_ver.json"
        items = [{"sku": "A", "unit_price_cents": 100, "quantity": 1}]
        order = create_order(items)
        with self.assertRaises(ValueError):
            save_order(file_path, order, expected_version=True)


if __name__ == "__main__":
    unittest.main()
