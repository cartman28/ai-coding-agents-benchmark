"""Unit tests for journal append, hash chaining, replay, and atomic snapshots."""

import json
import tempfile
import unittest
from pathlib import Path

from pricebot.inventory import Inventory
from pricebot.journal import (
    append_journal,
    load_snapshot,
    replay_journal,
    write_snapshot,
)


class JournalTests(unittest.TestCase):
    def test_append_and_replay_journal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "journal.ndjson"
            append_journal(p, "initialize", {"initial_stock": {"SKU1": 10, "SKU2": 20}})
            append_journal(p, "reserve", {"reservation_id": "res-1", "items": {"SKU1": 3}})
            append_journal(p, "commit", {"reservation_id": "res-1"})
            append_journal(p, "restock", {"items": {"SKU1": 5}})

            inv = replay_journal(p)
            snap = inv.snapshot()
            self.assertEqual(snap["on_hand"]["SKU1"], 12)  # 10 - 3 + 5
            self.assertEqual(snap["on_hand"]["SKU2"], 20)
            self.assertEqual(snap["reservations"]["res-1"]["status"], "committed")

    def test_journal_tampering_detection(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "journal.ndjson"
            append_journal(p, "initialize", {"initial_stock": {"SKU1": 10}})
            append_journal(p, "reserve", {"reservation_id": "res-1", "items": {"SKU1": 3}})

            # Tamper with journal file content
            lines = p.read_text(encoding="utf-8").splitlines()
            # Modify data in second line
            entry = json.loads(lines[1])
            entry["data"]["items"] = {"SKU1": 99}
            # Keep hash invalid to simulate tampering
            lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                replay_journal(p)

    def test_journal_broken_hash_chain(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "journal.ndjson"
            append_journal(p, "initialize", {"initial_stock": {"SKU1": 10}})
            append_journal(p, "reserve", {"reservation_id": "res-1", "items": {"SKU1": 3}})

            lines = p.read_text(encoding="utf-8").splitlines()
            # Change prev_hash of second entry
            entry = json.loads(lines[1])
            entry["prev_hash"] = "0" * 64
            lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                replay_journal(p)

    def test_journal_truncation_or_malformed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "journal.ndjson"
            p.write_text("{broken json\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                replay_journal(p)

            p.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                replay_journal(p)

    def test_snapshot_write_and_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snapshot.json"
            inv = Inventory({"SKU1": 10})
            inv.reserve("res-1", {"SKU1": 2})
            state = inv.snapshot()

            write_snapshot(p, state)
            loaded = load_snapshot(p)
            self.assertEqual(loaded, state)

    def test_snapshot_missing_or_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "missing.json"
            with self.assertRaises(FileNotFoundError):
                load_snapshot(p)

            p.write_text("not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_snapshot(p)

            p.write_text('{"on_hand": {}}', encoding="utf-8")  # missing reservations
            with self.assertRaises(ValueError):
                load_snapshot(p)

    def test_snapshot_atomicity(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "snapshot.json"
            state1 = Inventory({"SKU1": 10}).snapshot()
            write_snapshot(p, state1)
            before_bytes = p.read_bytes()

            state2 = Inventory({"SKU1": 20}).snapshot()
            # Test that if os.replace fails, old snapshot is preserved and temp cleaned up
            from unittest.mock import patch
            with patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_snapshot(p, state2)

            self.assertEqual(p.read_bytes(), before_bytes)
            # Ensure no orphan tmp files left in directory
            files = list(Path(d).iterdir())
            self.assertEqual(len(files), 1)

    def test_replay_impossible_transition(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "journal.ndjson"
            append_journal(p, "initialize", {"initial_stock": {"SKU1": 5}})
            # Trying to commit non-existent reservation
            append_journal(p, "commit", {"reservation_id": "nonexistent"})

            with self.assertRaises(ValueError):
                replay_journal(p)


if __name__ == "__main__":
    unittest.main()
