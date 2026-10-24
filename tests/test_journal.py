import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pricebot.inventory import Inventory
from pricebot.journal import (
    append_journal,
    load_journal,
    load_snapshot,
    replay_journal,
    write_snapshot,
)


class JournalTests(unittest.TestCase):
    def test_append_and_load_journal(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.ndjson"
            h1 = append_journal(path, "initial_stock", {"initial_stock": {"A": 10}}, prev_hash=None, seq=1)
            h2 = append_journal(path, "reserve", {"reservation_id": "r1", "items": {"A": 2}}, prev_hash=h1, seq=2)

            entries = load_journal(path)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["seq"], 1)
            self.assertEqual(entries[0]["prev_hash"], None)
            self.assertEqual(entries[0]["hash"], h1)
            self.assertEqual(entries[1]["seq"], 2)
            self.assertEqual(entries[1]["prev_hash"], h1)
            self.assertEqual(entries[1]["hash"], h2)

    def test_hash_chain_validation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.ndjson"
            h1 = append_journal(path, "initial_stock", {"initial_stock": {"A": 10}}, prev_hash=None, seq=1)
            append_journal(path, "restock", {"items": {"A": 5}}, prev_hash=h1, seq=2)

            # Valid load should pass
            entries = load_journal(path)
            self.assertEqual(len(entries), 2)

    def test_journal_tampering_detection(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.ndjson"
            h1 = append_journal(path, "initial_stock", {"initial_stock": {"A": 10}}, prev_hash=None, seq=1)
            append_journal(path, "restock", {"items": {"A": 5}}, prev_hash=h1, seq=2)

            lines = path.read_text(encoding="utf-8").splitlines()
            # Tamper with first line data
            entry = json.loads(lines[0])
            entry["data"]["initial_stock"]["A"] = 999
            lines[0] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_journal(path)

    def test_journal_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.ndjson"
            append_journal(path, "initial_stock", {"initial_stock": {"A": 10}}, prev_hash=None, seq=1)

            # Append truncated JSON
            with open(path, "a", encoding="utf-8") as f:
                f.write('{"seq":2,"prev_hash":"abc"')

            with self.assertRaises(ValueError):
                load_journal(path)

    def test_replay_journal(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.ndjson"
            h1 = append_journal(path, "initial_stock", {"initial_stock": {"A": 10}}, prev_hash=None, seq=1)
            h2 = append_journal(path, "reserve", {"reservation_id": "r1", "items": {"A": 3}}, prev_hash=h1, seq=2)
            h3 = append_journal(path, "commit", {"reservation_id": "r1"}, prev_hash=h2, seq=3)
            h4 = append_journal(path, "restock", {"items": {"A": 5}}, prev_hash=h3, seq=4)

            inv = replay_journal(path)
            self.assertEqual(inv.on_hand["A"], 12)  # 10 - 3 + 5 = 12
            self.assertEqual(inv.reservations["r1"]["status"], "committed")

    def test_replay_rejects_impossible(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "journal.ndjson"
            h1 = append_journal(path, "initial_stock", {"initial_stock": {"A": 2}}, prev_hash=None, seq=1)
            append_journal(path, "reserve", {"reservation_id": "r1", "items": {"A": 5}}, prev_hash=h1, seq=2)

            with self.assertRaises(ValueError):
                replay_journal(path)

    def test_write_and_load_snapshot(self):
        inv = Inventory({"A": 10})
        inv.reserve("r1", {"A": 4})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            write_snapshot(path, inv)
            snap = load_snapshot(path)
            self.assertEqual(snap["on_hand"], {"A": 10})
            self.assertEqual(snap["reservations"]["r1"]["items"], {"A": 4})

    def test_snapshot_atomic_replace_failure(self):
        inv = Inventory({"A": 10})
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            write_snapshot(path, inv)
            before = path.read_bytes()

            with patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_snapshot(path, {"on_hand": {"A": 20}, "reservations": {}})

            self.assertEqual(path.read_bytes(), before)
            # Ensure no orphan temp files left behind
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()), ["snapshot.json"])

    def test_snapshot_invalid_schema(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "snapshot.json"
            path.write_text('{"invalid": true}', encoding="utf-8")
            with self.assertRaises(ValueError):
                load_snapshot(path)

    def test_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "missing.json"
            with self.assertRaises(FileNotFoundError):
                load_snapshot(path)


if __name__ == "__main__":
    unittest.main()
