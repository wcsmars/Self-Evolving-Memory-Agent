"""Behavioral tests for auditable, reversible memory evolution."""

import json
import tempfile
import unittest

from memory_agent.evolution import EvolutionEngine, ProposalError
from memory_agent.store import Store


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(self.directory.name)
        self.engine = EvolutionEngine(self.store)

    def add(self, content="The demo service runs in region A.", **kwargs):
        data = {"content": content, "valid_from": "2024-01-01", "source_text": "Original evidence: " + content}
        data.update(kwargs)
        return self.store.add_memory(**data)

    def proposal(self, kind):
        return next(p for p in self.engine.review() if p["kind"] == kind)

    def test_exact_duplicate_keeps_sources_and_can_be_undone(self):
        first = self.add(source="conversation-1")
        second = self.add(source="conversation-2", source_text="Independent source for the same statement.")
        p = self.proposal("deduplicate")
        self.assertEqual(len(self.store.list_memories()), 2)  # review never mutates memories
        self.assertEqual(set(p["evidence_ids"]), {first["source_id"], second["source_id"]})
        applied = self.engine.apply(p["id"])
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(self.store.list_memories()), 1)
        self.assertEqual(len(self.store.list_memories(include_inactive=True)), 2)
        self.assertTrue(self.store.get_source(first["source_id"]))
        self.assertTrue(self.store.get_source(second["source_id"]))
        self.assertEqual(self.engine.undo(p["id"])["status"], "reverted")
        self.assertEqual(len(self.store.list_memories()), 2)
        history = json.dumps(self.store.list_events())
        self.assertIn(p["id"], history)
        self.assertIn("proposal_reverted", history)

    def test_different_intervals_are_not_duplicates(self):
        self.add(valid_from="2024-01-01")
        self.add(valid_from="2024-02-01")
        self.assertFalse(self.engine.review())

    def test_contradiction_preserves_historical_lookup(self):
        old = self.add("The release status is planned.", kind="fact", subject="demo-release", predicate="status", value="planned")
        new = self.add("The release status is complete.", kind="fact", subject="demo-release", predicate="status", value="complete", valid_from="2024-02-01")
        proposal = self.proposal("supersede")
        self.engine.apply(proposal["id"])
        january = self.store.list_memories(as_of="2024-01-15")
        boundary = self.store.list_memories(as_of="2024-02-01")
        self.assertEqual([m["id"] for m in january], [old["id"]])
        self.assertEqual([m["id"] for m in boundary], [new["id"]])
        self.assertEqual(self.store.get_memory(old["id"])["content"], old["content"])
        self.engine.undo(proposal["id"])
        self.assertIsNone(self.store.get_memory(old["id"])["valid_until"])

    def test_multi_step_changes_produce_contiguous_history(self):
        memories = [self.add("Status is " + value, kind="fact", subject="demo", predicate="status", value=value, valid_from=date)
                    for value, date in (("planned", "2024-01-01"), ("started", "2024-02-01"), ("complete", "2024-03-01"))]
        proposal = self.proposal("supersede")
        self.engine.apply(proposal["id"])
        for date, expected in zip(("2024-01-15", "2024-02-15", "2024-03-15"), memories):
            self.assertEqual([m["id"] for m in self.store.list_memories(as_of=date)], [expected["id"]])

    def test_nonoverlapping_updates_do_not_require_review(self):
        self.add("Planned", kind="fact", subject="demo", predicate="status", value="planned", valid_until="2024-02-01")
        self.add("Complete", kind="fact", subject="demo", predicate="status", value="complete", valid_from="2024-02-01")
        self.assertFalse(self.engine.review())

    def test_equal_date_contradictions_cannot_select_a_winner(self):
        self.add("The budget is 10.", kind="fact", subject="demo", predicate="budget", value="10")
        self.add("The budget is 20.", kind="fact", subject="demo", predicate="budget", value="20")
        proposal = self.proposal("contradiction")
        self.assertFalse(proposal["actionable"])
        with self.assertRaisesRegex(ProposalError, "manual"):
            self.engine.apply(proposal["id"])
        self.assertEqual(len(self.store.list_memories()), 2)
        self.assertEqual(self.engine.get_proposal(proposal["id"])["status"], "pending")

    def test_new_contradictory_evidence_invalidates_prior_hypothesis(self):
        older = self.add("Status is planned", kind="fact", subject="demo", predicate="status", value="planned")
        self.add("Status is started", kind="fact", subject="demo", predicate="status", value="started", valid_from="2024-02-01")
        proposal = self.proposal("supersede")
        self.add("Status is blocked", kind="fact", subject="demo", predicate="status", value="blocked", valid_from="2024-02-01")
        with self.assertRaisesRegex(ProposalError, "Relevant facts changed"):
            self.engine.apply(proposal["id"])
        self.assertIsNone(self.store.get_memory(older["id"])["valid_until"])
        self.assertEqual(self.engine.get_proposal(proposal["id"])["status"], "pending")

    def test_stale_multi_record_proposal_is_atomic(self):
        memories = [self.add("Record {}.".format(i), kind="fact" if i % 2 else "event", topic="research") for i in range(9)]
        proposal = self.proposal("reorganize")
        # Edit the final planned record so a naive write-as-you-go implementation
        # would have changed earlier records before finding the stale version.
        final_id = proposal["changes"][-1]["id"]
        self.store.update_memory(final_id, {"importance": 0.7})
        with self.assertRaises((ValueError, RuntimeError)):
            self.engine.apply(proposal["id"])
        self.assertTrue(all(self.store.get_memory(m["id"])["topic"] == "research" for m in memories))
        self.assertEqual(self.engine.get_proposal(proposal["id"])["status"], "pending")

    def test_keeper_change_also_invalidates_duplicate_proposal(self):
        self.add()
        self.add()
        proposal = self.proposal("deduplicate")
        keeper = next(c["id"] for c in proposal["changes"] if not c["changes"])
        self.store.update_memory(keeper, {"content": "The service moved to region B."})
        with self.assertRaises((ValueError, RuntimeError)):
            self.engine.apply(proposal["id"])
        self.assertEqual(len(self.store.list_memories()), 2)

    def test_undo_does_not_overwrite_later_edits(self):
        memory = self.add("I prefer concise explanations.")
        proposal = self.proposal("classify")
        self.engine.apply(proposal["id"])
        self.store.update_memory(memory["id"], {"content": "I prefer detailed explanations."})
        with self.assertRaises((ValueError, RuntimeError)):
            self.engine.undo(proposal["id"])
        current = self.store.get_memory(memory["id"])
        self.assertEqual(current["content"], "I prefer detailed explanations.")
        self.assertEqual(current["kind"], "preference")
        self.assertEqual(self.engine.get_proposal(proposal["id"])["status"], "applied")

    def test_review_is_idempotent_and_remembers_rejection(self):
        self.add("I prefer concise explanations.")
        first = self.engine.review()
        self.assertEqual(first, self.engine.review())
        self.engine.reject(first[0]["id"])
        self.assertFalse(self.engine.review())
        self.assertEqual(len(self.engine.list_proposals()), 1)
        with self.assertRaises(ProposalError):
            self.engine.apply(first[0]["id"])

    def test_reorganization_can_be_undone_without_changing_content(self):
        memories = [self.add("Record {}.".format(i), kind="fact" if i % 2 else "event", topic="research") for i in range(9)]
        proposal = self.proposal("reorganize")
        self.engine.apply(proposal["id"])
        for before in memories:
            after = self.store.get_memory(before["id"])
            self.assertEqual(after["content"], before["content"])
            self.assertEqual(after["topic"], "research/" + before["kind"])
        self.engine.undo(proposal["id"])
        self.assertTrue(all(self.store.get_memory(m["id"])["topic"] == "research" for m in memories))

    def test_evidence_and_proposals_survive_new_engine_instance(self):
        self.add("I prefer written notes.")
        proposal = self.proposal("classify")
        reloaded = EvolutionEngine(Store(self.directory.name))
        self.assertEqual(reloaded.get_proposal(proposal["id"]), proposal)
        reloaded.apply(proposal["id"])
        self.assertEqual(self.engine.get_proposal(proposal["id"])["status"], "applied")


if __name__ == "__main__":
    unittest.main()
