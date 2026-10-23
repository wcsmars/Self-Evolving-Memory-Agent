"""Tests for the benchmark's safety and interpretation, not timing targets."""
import json
import unittest
from pathlib import Path

import xml.etree.ElementTree as ElementTree

from memory_agent.benchmark import render_svg, run_benchmark, synthetic_dataset


class BenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = run_benchmark()

    def test_all_metrics_are_bounded_and_complete(self):
        self.assertEqual(self.report["summary"]["case_count"], 22)
        self.assertEqual(len(self.report["cases"]), 110)
        for strategy in self.report["strategies"]:
            for metric in ("recall_at_3", "top1_accuracy", "abstention_accuracy", "stale_hit_rate"):
                self.assertGreaterEqual(strategy[metric], 0)
                self.assertLessEqual(strategy[metric], 1)
            self.assertGreaterEqual(strategy["mean_latency_ms"], 0)
            self.assertGreaterEqual(strategy["p95_latency_ms"], 0)
            self.assertEqual(strategy["answerable_cases"] + strategy["unanswerable_cases"], 22)

    def test_temporal_strategies_never_return_out_of_interval_memories(self):
        for result in self.report["cases"]:
            if result["strategy"] != "naive_keyword":
                self.assertEqual(result["stale_hits"], 0, result)
        naive = next(item for item in self.report["strategies"] if item["id"] == "naive_keyword")
        self.assertGreater(naive["stale_records"], 0)

    def test_boundary_fixture_has_only_new_expected_value(self):
        case = next(item for item in synthetic_dataset()["cases"] if item["id"] == "atlas_boundary")
        self.assertEqual(case["expected"], ["atlas_beta"])
        for result in self.report["cases"]:
            if result["case_id"] == case["id"] and result["strategy"] != "naive_keyword":
                self.assertNotIn("atlas_pending", result["returned"])
                self.assertNotIn("atlas_shipped", result["returned"])

    def test_semantic_limit_is_visible_in_fixture_and_result(self):
        cases = [case for case in synthetic_dataset()["cases"] if case["category"] == "semantic_gap"]
        self.assertEqual(len(cases), 2)
        # The test enforces honest coverage of a known limitation, not the failure
        # itself: a future semantic implementation is allowed to answer correctly.
        for case in cases:
            self.assertTrue(case["expected"])
            self.assertTrue(any(result["case_id"] == case["id"] for result in self.report["cases"]))

    def test_duplicate_evolution_preserves_evidence_and_supports_undo(self):
        evolution = self.report["evolution"]
        self.assertTrue(evolution["applied"])
        self.assertEqual(evolution["before_active"], 2)
        self.assertEqual(evolution["after_active"], 1)
        self.assertEqual(evolution["records_retained"], 2)
        self.assertTrue(evolution["evidence_retained"])
        self.assertTrue(evolution["meaning_preserved"])
        self.assertEqual(evolution["undo_restored_count"], 2)

    def test_readable_fixture_matches_runtime_data(self):
        path = Path(__file__).resolve().parents[1] / "benchmarks" / "synthetic.json"
        self.assertEqual(json.loads(path.read_text()), synthetic_dataset())

    def test_chart_is_well_formed_and_labels_every_strategy(self):
        svg = render_svg({**self.report, "summary": {**self.report["summary"], "description": "a < b & c"}})
        root = ElementTree.fromstring(svg)
        self.assertTrue(root.tag.endswith("svg"))
        for strategy in self.report["strategies"]:
            self.assertIn(strategy["name"], svg)
        self.assertIn("a &lt; b &amp; c", svg)


if __name__ == "__main__":
    unittest.main()
