import tempfile
import unittest

from memory_agent.retrieval import Retriever
from memory_agent.store import Store


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.retriever = Retriever(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def add(self, text, **kwargs):
        kwargs.setdefault("valid_from", "2020-01-01")
        return self.store.add_memory(text, **kwargs)

    def test_current_and_historical_retrieval_exclude_future_and_expired_facts(self):
        previous = self.add("Atlas application status: pending", subject="Atlas", predicate="application status",
                            value="pending", valid_until="2021-01-01")
        current = self.add("Atlas application status: interview", subject="Atlas", predicate="application status",
                           value="interview", valid_from="2021-01-01")
        self.add("Atlas application status: hired", valid_from="2099-01-01")
        historic = self.retriever.search("Atlas application status", as_of="2020-06-01")
        self.assertEqual([r["memory"]["id"] for r in historic["results"]], [previous["id"]])
        now = self.retriever.search("Atlas application status")
        self.assertEqual([r["memory"]["id"] for r in now["results"]], [current["id"]])
        at_boundary = self.retriever.search("Atlas application status", as_of="2021-01-01")
        self.assertEqual([r["memory"]["id"] for r in at_boundary["results"]], [current["id"]])

    def test_recency_is_relative_to_requested_time(self):
        self.add("Atlas milestone launch", valid_from="2020-01-01")
        result = self.retriever.search("Atlas milestone", as_of="2020-01-01")
        self.assertAlmostEqual(result["results"][0]["signals"]["recency"], 1.0)

    def test_feedback_improves_relevant_record_ranking_and_survives_restart(self):
        self.add("Atlas project uses Python")
        self.add("Atlas project uses Python")
        first = self.retriever.search("Atlas project")
        selected = first["results"][-1]["memory"]["id"]
        state = self.retriever.feedback(first["retrieval_id"], [selected])
        self.assertEqual(state["feedback_count"], 1)
        restarted = Retriever(Store(self.temp.name))
        next_result = restarted.search("Atlas project")
        self.assertEqual(next_result["results"][0]["memory"]["id"], selected)
        self.assertGreater(next_result["results"][0]["signals"]["usefulness"], 0.5)
        self.assertEqual(restarted.learning_state()["feedback_count"], 1)

    def test_identical_feedback_is_idempotent_and_conflicting_feedback_rejected(self):
        memory = self.add("Atlas project uses Python")
        result = self.retriever.search("Atlas")
        self.retriever.feedback(result["retrieval_id"], [memory["id"]])
        self.retriever.feedback(result["retrieval_id"], [memory["id"], memory["id"]])
        with self.assertRaises(ValueError):
            self.retriever.feedback(result["retrieval_id"], [])
        self.assertEqual(self.retriever.learning_state()["feedback_count"], 1)

    def test_feedback_rejects_unseen_ids_without_learning(self):
        self.add("Atlas project uses Python")
        unrelated = self.add("Garden compost instructions")
        result = self.retriever.search("Atlas")
        with self.assertRaises(ValueError):
            self.retriever.feedback(result["retrieval_id"], [unrelated["id"]])
        with self.assertRaises(KeyError):
            self.retriever.feedback("r_missing", [])
        self.assertEqual(self.retriever.learning_state()["feedback_count"], 0)

    def test_unknown_query_abstains_even_with_high_confidence_memory(self):
        self.add("Atlas project uses Python", confidence=1, importance=1)
        for strategy in ("hybrid", "lexical", "vector"):
            result = self.retriever.search("Japanese volcanoes", strategy=strategy)
            self.assertEqual(result["results"], [])

    def test_stopword_only_query_abstains(self):
        self.add("Atlas project uses Python")
        self.assertEqual(self.retriever.search("what is it about")["results"], [])

    def test_topic_and_kind_labels_alone_do_not_count_as_evidence(self):
        self.add("Saffron rice needs a long soak.", kind="fact", topic="projects/kitchen")
        for query in ("quasar violin facts", "unknown project notes"):
            with self.subTest(query=query):
                self.assertEqual(self.retriever.search(query)["results"], [])
        self.assertEqual(len(self.retriever.search("saffron facts")["results"]), 1)

    def test_singular_and_plural_forms_match(self):
        for stored, query in (("Two bugs were fixed", "bug"), ("Packed the boxes", "box"),
                              ("New classes start Monday", "class"), ("Quarterly releases are planned", "release")):
            with self.subTest(query=query):
                memory = self.add(stored)
                self.assertIn(memory["id"], [r["memory"]["id"] for r in self.retriever.search(query)["results"]])

    def test_meaningful_short_words_and_chinese_phrases_are_searchable(self):
        us = self.add("US exports rose in May")
        memory = self.add("Project uses persistent memory")
        chinese = self.add("Atlas 項目狀態是測試版")
        self.assertEqual(self.retriever.search("US")["results"][0]["memory"]["id"], us["id"])
        self.assertEqual(self.retriever.search("memory")["results"][0]["memory"]["id"], memory["id"])
        self.assertEqual(self.retriever.search("項目狀態")["results"][0]["memory"]["id"], chinese["id"])

    def test_input_validation(self):
        for query in ("", "  ", None, 123, "a" * 4001):
            with self.assertRaises(ValueError):
                self.retriever.search(query)
        for limit in (0, -1, 101, True, 1.2):
            with self.assertRaises(ValueError):
                self.retriever.search("Atlas", limit=limit)
        with self.assertRaises(ValueError):
            self.retriever.search("Atlas", strategy="made-up")

    def test_full_context_baseline_obeys_temporal_visibility(self):
        earlier = self.add("Older generic record", valid_from="2020-01-01")
        later = self.add("Newer generic record", valid_from="2020-02-01")
        self.add("Future record", valid_from="2099-01-01")
        result = self.retriever.search("unrelated", strategy="full_context", as_of="2020-03-01")
        self.assertEqual([r["memory"]["id"] for r in result["results"]], [later["id"], earlier["id"]])

    def test_record_false_does_not_write_trace(self):
        self.add("Atlas project uses Python")
        result = self.retriever.search("Atlas", record=False)
        self.assertIsNone(result["retrieval_id"])
        with self.store.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM retrieval_traces").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
