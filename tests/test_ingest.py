"""Conservative extraction, evidence preservation and the CLI import workflow."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from memory_agent.cli import main
from memory_agent.ingest import extract, ingest
from memory_agent.store import Store


class IngestionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "private-data")

    def tearDown(self):
        self.temp.cleanup()

    def test_preserves_evidence_and_only_extracts_explicit_structures(self):
        text = (
            "# Original transcript\n"
            "- [2020-03-04] Atlas | owner | Taylor\n"
            "I prefer short summaries with sources.\n"
            "Always retain original evidence.\n"
            "Perhaps the plan is approved, but the note is ambiguous.\n"
        )
        memories = ingest(self.store, text, source="Synthetic conversation", topic="projects/atlas")
        self.assertEqual([m["kind"] for m in memories], ["fact", "preference", "procedure", "note"])
        fact = memories[0]
        self.assertEqual((fact["subject"], fact["predicate"], fact["value"]), ("Atlas", "owner", "Taylor"))
        self.assertEqual(fact["content"], "[2020-03-04] Atlas | owner | Taylor")
        self.assertTrue(fact["valid_from"].startswith("2020-03-04T00:00:00"))
        self.assertIsNone(memories[-1]["subject"])
        self.assertEqual(memories[-1]["content"], "Perhaps the plan is approved, but the note is ambiguous.")
        for memory in memories:
            self.assertEqual(self.store.get_source(memory["source_id"])["text"], text)
        restarted = Store(Path(self.temp.name) / "private-data")
        self.assertEqual(len(restarted.list_memories(include_inactive=True)), 4)

    def test_invalid_later_line_does_not_leave_partial_import(self):
        for text in (
            "A valid first line.\n[2020-13-02] Invalid date.",
            "A valid first line.\nAtlas | owner | " + "x" * 10_001,
        ):
            with self.subTest(text=text[:50]):
                with self.assertRaises(ValueError):
                    ingest(self.store, text)
                self.assertEqual(self.store.list_memories(include_inactive=True), [])

    def test_empty_and_excessive_imports_are_rejected(self):
        for text in ("", "   ", "# Headers only", None, "\n".join("Record %s" % i for i in range(201))):
            with self.subTest(text=str(text)[:30]):
                with self.assertRaises(ValueError):
                    extract(text)

    def test_unrecognized_pipe_syntax_is_preserved_as_note(self):
        line = "Atlas | owner | Taylor | unconfirmed"
        item = extract(line)[0]
        self.assertEqual(item["kind"], "note")
        self.assertEqual(item["content"], line)
        self.assertNotIn("subject", item)

    def cli(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--root", str(Path(self.temp.name) / "cli-data"), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_cli_import_search_and_error_exit(self):
        path = Path(self.temp.name) / "notes.txt"
        path.write_text("[2020-01-01] Atlas | owner | Taylor\n", encoding="utf-8")
        code, output, errors = self.cli("ingest", str(path), "--topic", "projects/atlas")
        self.assertEqual(code, 0, errors)
        memory = json.loads(output)["memories"][0]
        code, output, errors = self.cli("search", "Atlas owner", "--as-of", "2020-02-01")
        self.assertEqual(code, 0, errors)
        self.assertEqual(json.loads(output)["results"][0]["memory"]["id"], memory["id"])
        code, output, errors = self.cli("ingest", str(Path(self.temp.name) / "missing.txt"))
        self.assertEqual(code, 1)
        self.assertIn("Error:", errors)

    def test_cli_update_is_version_checked_and_preserves_original_source(self):
        code, output, errors = self.cli("add", "Atlas status: draft", "--valid-from", "2020-01-01")
        self.assertEqual(code, 0, errors)
        memory = json.loads(output)
        arguments = ("update", memory["id"], "--version", str(memory["version"]), "--changes", '{"content":"Atlas status: reviewed"}')
        code, output, errors = self.cli(*arguments)
        self.assertEqual(code, 0, errors)
        updated = json.loads(output)
        self.assertEqual(updated["content"], "Atlas status: reviewed")
        code, output, errors = self.cli(*arguments)
        self.assertEqual(code, 1)
        self.assertIn("changed since review", errors)
        reopened = Store(Path(self.temp.name) / "cli-data")
        self.assertEqual(reopened.get_source(memory["source_id"])["text"], "Atlas status: draft")


if __name__ == "__main__":
    unittest.main()
