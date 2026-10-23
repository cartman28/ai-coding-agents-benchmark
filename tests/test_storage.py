import json
import os
import tempfile
import unittest

from pricebot.orders import create_order
from pricebot.storage import save_order, load_order


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.test_dir.name, "order.json")

    def tearDown(self):
        self.test_dir.cleanup()

    def test_save_and_load_order(self):
        order = create_order([{"sku": "S1", "unit_price_cents": 1500, "quantity": 1}])
        save_order(self.file_path, order)
        loaded = load_order(self.file_path)
        self.assertEqual(order, loaded)
        # Ensure load_order returns a fresh dict (not same object reference)
        self.assertIsNot(order, loaded)

    def test_serialization_format(self):
        order = create_order([{"sku": "S1", "unit_price_cents": 100, "quantity": 1}])
        save_order(self.file_path, order)
        with open(self.file_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Verify no spaces after comma or colon (separators=(',', ':'))
        self.assertNotIn(", ", content)
        self.assertNotIn(": ", content)
        # Verify sorted keys
        parsed_raw = json.loads(content)
        self.assertEqual(content, json.dumps(parsed_raw, sort_keys=True, ensure_ascii=False, separators=(',', ':')))

    def test_optimistic_concurrency_success(self):
        order = create_order([{"sku": "S1", "unit_price_cents": 100, "quantity": 1}])
        save_order(self.file_path, order, expected_version=None)
        
        # Modify order (version 2)
        order["version"] = 2
        order["history"].append({"version": 2, "event": "submit", "status": "submitted"})
        order["status"] = "submitted"
        
        save_order(self.file_path, order, expected_version=1)
        loaded = load_order(self.file_path)
        self.assertEqual(loaded["version"], 2)

    def test_optimistic_concurrency_failure(self):
        order = create_order([{"sku": "S1", "unit_price_cents": 100, "quantity": 1}])
        save_order(self.file_path, order)
        
        # Mismatched expected version
        with self.assertRaises(ValueError):
            save_order(self.file_path, order, expected_version=2)
            
        # Invalid expected version type (bool or non-int)
        with self.assertRaises(ValueError):
            save_order(self.file_path, order, expected_version=True)

    def test_missing_file_expected_version(self):
        order = create_order([{"sku": "S1", "unit_price_cents": 100, "quantity": 1}])
        # Missing file accepts expected_version=None or 0
        save_order(self.file_path, order, expected_version=0)
        self.assertTrue(os.path.exists(self.file_path))
        
        os.remove(self.file_path)
        with self.assertRaises(ValueError):
            save_order(self.file_path, order, expected_version=1)

    def test_load_nonexistent_file(self):
        with self.assertRaises(FileNotFoundError):
            load_order(os.path.join(self.test_dir.name, "nonexistent.json"))

    def test_corrupt_json(self):
        with open(self.file_path, "w", encoding="utf-8") as f:
            f.write("{invalid json")
        with self.assertRaises(ValueError):
            load_order(self.file_path)

    def test_malformed_order_schema(self):
        malformed = {"order_id": "ord_123", "version": 1}
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(malformed, f)
        with self.assertRaises(ValueError):
            load_order(self.file_path)

    def test_missing_parent_directory(self):
        order = create_order([{"sku": "S1", "unit_price_cents": 100, "quantity": 1}])
        non_existent_path = os.path.join(self.test_dir.name, "non_existent_dir", "order.json")
        with self.assertRaises(FileNotFoundError):
            save_order(non_existent_path, order)


if __name__ == "__main__":
    unittest.main()
