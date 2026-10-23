"""Behavioral tests for persistence, provenance, temporal reads, and safe edits."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from memory_agent.store import Store, normalize_time


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)

    def remember(self, content="A durable memory", **kwargs):
        kwargs.setdefault("valid_from", "2024-01-01")
        return self.store.add_memory(content, **kwargs)

    def test_memory_and_original_evidence_survive_reopening(self):
        memory = self.remember(
            "The project uses SQLite.",
            kind="fact",
            topic="projects/storage",
            subject="project",
            predicate="database",
            value="SQLite",
            source="planning-session",
            source_text="We agreed to use SQLite for this project.",
        )
        reopened = Store(self.root)
        self.assertEqual(reopened.get_memory(memory["id"]), memory)
        evidence = reopened.get_source(memory["source_id"])
        self.assertEqual(evidence["text"], "We agreed to use SQLite for this project.")
        self.assertEqual(evidence["label"], "planning-session")
        self.assertTrue(any((self.root / "evidence").iterdir()))

    def test_temporal_interval_is_left_closed_right_open(self):
        past = self.remember("Status: pending", valid_from="2024-01-01", valid_until="2024-02-01")
        current = self.remember("Status: invited", valid_from="2024-02-01")
        self.assertEqual(self.store.list_memories(as_of="2023-12-31"), [])
        self.assertEqual([m["id"] for m in self.store.list_memories(as_of="2024-01-01")], [past["id"]])
        self.assertEqual([m["id"] for m in self.store.list_memories(as_of="2024-02-01")], [current["id"]])
        self.assertEqual(len(self.store.list_memories(include_inactive=True)), 2)

    def test_timezone_offsets_refer_to_same_instant(self):
        self.assertEqual(normalize_time("2024-06-01T08:30:00+08:00"), normalize_time("2024-06-01T00:30:00Z"))
        self.assertEqual(normalize_time("2024-06-01"), normalize_time("2024-06-01T00:00:00Z"))

    def test_future_and_archived_memories_are_not_current(self):
        future = self.remember("A distant future event", valid_from="2999-01-01")
        archived = self.remember("An archived note")
        self.store.update_memory(archived["id"], {"status": "archived"})
        self.assertEqual(self.store.list_memories(), [])
        all_ids = {m["id"] for m in self.store.list_memories(include_inactive=True)}
        self.assertEqual(all_ids, {future["id"], archived["id"]})

    def test_filters_do_not_mix_topics_or_types(self):
        target = self.remember("Use short responses", kind="preference", topic="profile/style")
        self.remember("Enjoys cycling", kind="preference", topic="profile/hobbies")
        self.remember("Style discussion took place", kind="event", topic="profile/style")
        result = self.store.list_memories(topic="profile/style", kind="preference")
        self.assertEqual([m["id"] for m in result], [target["id"]])

    def test_validation_rejects_invalid_values_before_writing(self):
        cases = [
            {"content": "   "},
            {"kind": "unsupported"},
            {"confidence": -0.01},
            {"confidence": 1.01},
            {"importance": float("nan")},
            {"importance": float("inf")},
            {"valid_from": "not-a-date"},
            {"valid_from": "2024-02-01", "valid_until": "2024-01-01"},
            {"valid_from": "2024-02-01", "valid_until": "2024-02-01"},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                args = {"content": "Some content", "valid_from": "2024-01-01"}
                args.update(fields)
                with self.assertRaises(ValueError):
                    self.store.add_memory(**args)
        self.assertEqual(self.store.list_memories(include_inactive=True), [])

    def test_topic_cannot_escape_managed_directory(self):
        for topic in ["../escape", "/tmp/escape", "a/../../escape", "a\\..\\escape"]:
            with self.subTest(topic=topic):
                with self.assertRaises(ValueError):
                    self.remember(topic=topic)
        self.assertEqual(self.store.list_memories(include_inactive=True), [])

    def test_edit_preserves_source_and_records_both_revisions(self):
        memory = self.remember("Original interpretation", source_text="Exact original evidence.")
        evidence_before = self.store.get_source(memory["source_id"])
        updated = self.store.update_memory(memory["id"], {"content": "Corrected interpretation"}, expected_version=memory["version"])
        self.assertEqual(updated["version"], memory["version"] + 1)
        self.assertEqual(updated["source_id"], memory["source_id"])
        self.assertEqual(self.store.get_source(memory["source_id"]), evidence_before)
        audit = json.dumps(self.store.list_events())
        self.assertIn("Original interpretation", audit)
        self.assertIn("Corrected interpretation", audit)

    def test_stale_editor_cannot_overwrite_newer_change(self):
        memory = self.remember()
        winner = self.store.update_memory(memory["id"], {"content": "First editor"}, expected_version=memory["version"])
        with self.assertRaises(ValueError):
            self.store.update_memory(memory["id"], {"content": "Stale editor"}, expected_version=memory["version"])
        self.assertEqual(self.store.get_memory(memory["id"]), winner)

    def test_batch_is_atomic_when_one_version_is_stale(self):
        first = self.remember("First original")
        second = self.remember("Second original")
        self.store.update_memory(second["id"], {"content": "Second changed"})
        events_before = self.store.list_events()
        with self.assertRaises(ValueError):
            self.store.mutate_memories([
                {"id": first["id"], "expected_version": first["version"], "changes": {"content": "Must roll back"}},
                {"id": second["id"], "expected_version": second["version"], "changes": {"content": "Stale change"}},
            ])
        self.assertEqual(self.store.get_memory(first["id"]), first)
        self.assertEqual(self.store.get_memory(second["id"])["content"], "Second changed")
        self.assertEqual(self.store.list_events(), events_before)

    def test_batch_rejects_invalid_change_without_partial_commit(self):
        first = self.remember("Original first")
        second = self.remember("Original second")
        with self.assertRaises(ValueError):
            self.store.mutate_memories([
                {"id": first["id"], "expected_version": first["version"], "changes": {"content": "Should not persist"}},
                {"id": second["id"], "expected_version": second["version"], "changes": {"confidence": 7}},
            ])
        self.assertEqual(self.store.get_memory(first["id"]), first)
        self.assertEqual(self.store.get_memory(second["id"]), second)

    def test_identity_and_evidence_are_not_editable(self):
        memory = self.remember()
        for changes in [{"id": "replacement"}, {"source_id": "replacement"}, {"version": 999}]:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.store.update_memory(memory["id"], changes)
        self.assertEqual(self.store.get_memory(memory["id"]), memory)

    def test_missing_records_raise_key_error(self):
        with self.assertRaises(KeyError):
            self.store.get_memory("missing-memory")
        with self.assertRaises(KeyError):
            self.store.get_source("missing-source")
        with self.assertRaises(KeyError):
            self.store.update_memory("missing-memory", {"content": "Changed"})

    def test_markdown_export_preserves_unmanaged_user_files(self):
        memory = self.remember("A specific exported phrase", topic="projects/agent")
        user_file = self.root / "memory" / "my-personal-notes.md"
        user_file.write_text("Leave my unrelated notes intact.\n", encoding="utf-8")
        self.store.export_markdown()
        self.store.update_memory(memory["id"], {"topic": "projects/reorganized"})
        self.store.export_markdown()
        self.assertEqual(user_file.read_text(encoding="utf-8"), "Leave my unrelated notes intact.\n")
        markdown = list((self.root / "memory").rglob("*.md"))
        self.assertTrue(any(path.name == "INDEX.md" for path in markdown))
        self.assertTrue(any("A specific exported phrase" in path.read_text(encoding="utf-8") for path in markdown))

    def test_full_text_index_tracks_edits(self):
        memory = self.remember("zephyroldtoken evidence")
        self.store.update_memory(memory["id"], {"content": "zephyrnewtoken replacement"})
        connection = self.store.connection()
        self.addCleanup(connection.close)
        old_count = connection.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH ?", ("zephyroldtoken",)).fetchone()[0]
        new_count = connection.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH ?", ("zephyrnewtoken",)).fetchone()[0]
        self.assertEqual(old_count, 0)
        self.assertEqual(new_count, 1)

    def test_ingestion_batch_validation_leaves_no_partial_sources_or_memories(self):
        with self.assertRaises(ValueError):
            self.store.add_memories([
                {"content": "This valid record must not persist"},
                {"content": "Invalid record", "confidence": 2},
            ])
        stats = self.store.get_stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["sources"], 0)
        self.assertEqual(stats["events"], 0)

    def test_ingestion_batch_commits_together_and_exports_once(self):
        with patch.object(self.store, "export_markdown", wraps=self.store.export_markdown) as export:
            rows = self.store.add_memories([
                {"content": "First fact", "source_text": "  Original whitespace.\n"},
                {"content": "Second fact"},
            ])
        self.assertEqual(len(rows), 2)
        self.assertEqual(export.call_count, 1)
        self.assertEqual(self.store.get_stats()["total"], 2)
        self.assertEqual(self.store.get_source(rows[0]["source_id"])["text"], "  Original whitespace.\n")

    def test_projection_failure_does_not_hide_a_successful_commit(self):
        with patch.object(self.store, "export_markdown", side_effect=OSError("disk unavailable")):
            with self.assertLogs("memory_agent.store", level="WARNING"):
                row = self.store.add_memory("Durable even if export fails")
        self.assertEqual(self.store.get_memory(row["id"]), row)
        self.assertTrue(self.store.get_stats()["projection_errors"])
        self.store.refresh_projections()
        self.assertEqual(self.store.get_stats()["projection_errors"], [])

    def test_explicit_null_start_is_rejected_on_update(self):
        memory = self.remember()
        with self.assertRaises(ValueError):
            self.store.update_memory(memory["id"], {"valid_from": None}, expected_version=1)
        self.assertEqual(self.store.get_memory(memory["id"])["valid_from"], "2024-01-01T00:00:00.000000Z")

    def test_replace_cannot_rewrite_evidence_or_audit_events(self):
        import contextlib
        import sqlite3
        memory = self.remember()
        with contextlib.closing(self.store.connection()) as conn:
            for statement, args in (
                ("INSERT OR REPLACE INTO sources(id,text,label,created_at) VALUES (?,?,?,?)", (memory["source_id"], "forged", "x", "2000")),
                ("REPLACE INTO events(id,event_type,created_at,data) VALUES (1,'forged','2000','{}')", ()),
            ):
                with self.subTest(statement=statement[:20]):
                    with self.assertRaises(sqlite3.IntegrityError):
                        conn.execute(statement, args)
        self.assertNotEqual(self.store.get_source(memory["source_id"])["text"], "forged")

    def test_one_refused_projection_path_does_not_stop_the_rest(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.root / "memory" / "notes").symlink_to(outside.name, target_is_directory=True)
        refused = self.remember(topic="notes")
        later = self.remember("Written after the refused path", topic="zeta")
        errors = self.store.get_stats()["projection_errors"]
        self.assertTrue(any(refused["id"] in error for error in errors))
        self.assertTrue((self.root / "memory" / "zeta" / (later["id"] + ".md")).exists())
        self.assertIn(later["id"], (self.root / "memory" / "INDEX.md").read_text())
        self.assertEqual(list(Path(outside.name).iterdir()), [])

    def test_topic_capitalization_change_keeps_its_projection(self):
        memory = self.remember(topic="Projects/atlas")
        self.store.update_memory(memory["id"], {"topic": "projects/atlas"})
        self.assertTrue((self.root / "memory" / "projects" / "atlas" / (memory["id"] + ".md")).exists())

    def test_unchanged_projection_files_are_not_rewritten(self):
        memory = self.remember()
        path = self.root / "memory" / "inbox" / (memory["id"] + ".md")
        before = path.stat().st_mtime_ns
        self.remember("Another memory")
        self.assertEqual(path.stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
