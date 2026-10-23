"""Small, reproducible retrieval regression benchmark using synthetic memories.

This is deliberately not a model-quality or production-scale benchmark. The
time-blind baseline illustrates an expected design limitation, not a surprising
empirical discovery. No model API is called and user memory is never read.
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from time import perf_counter
from typing import Any

from .retrieval import Retriever
from .store import Store, normalize_time


def synthetic_dataset() -> dict[str, Any]:
    """Return the built-in fixture; benchmarks/synthetic.json is its readable copy."""
    memories = [
        {"key": "atlas_pending", "content": "Atlas release status is pending.", "subject": "Atlas", "predicate": "release status", "value": "pending", "topic": "projects/atlas", "valid_from": "2026-09-01", "valid_until": "2026-09-15"},
        {"key": "atlas_beta", "content": "Atlas release status is beta.", "subject": "Atlas", "predicate": "release status", "value": "beta", "topic": "projects/atlas", "valid_from": "2026-09-15", "valid_until": "2026-10-01"},
        {"key": "atlas_shipped", "content": "Atlas release status is shipped.", "subject": "Atlas", "predicate": "release status", "value": "shipped", "topic": "projects/atlas", "valid_from": "2026-10-01"},
        {"key": "lyra_seed", "content": "Lyra funding stage is seed.", "subject": "Lyra", "predicate": "funding stage", "value": "seed", "topic": "projects/lyra", "valid_from": "2026-07-01", "valid_until": "2026-09-10"},
        {"key": "lyra_series_a", "content": "Lyra funding stage is series A.", "subject": "Lyra", "predicate": "funding stage", "value": "series A", "topic": "projects/lyra", "valid_from": "2026-09-10"},
        {"key": "language_python", "content": "Robin's preferred programming language is Python.", "kind": "preference", "subject": "Robin", "predicate": "preferred programming language", "value": "Python", "topic": "profile/preferences", "valid_from": "2026-01-01", "valid_until": "2026-10-05"},
        {"key": "language_rust", "content": "Robin's preferred programming language is Rust.", "kind": "preference", "subject": "Robin", "predicate": "preferred programming language", "value": "Rust", "topic": "profile/preferences", "valid_from": "2026-10-05"},
        {"key": "coffee", "content": "Robin prefers black coffee.", "kind": "preference", "subject": "Robin", "predicate": "coffee preference", "value": "black coffee", "topic": "profile/preferences", "valid_from": "2026-01-01"},
        {"key": "lisbon_trip", "content": "Robin's Lisbon conference takes place during October 10 through October 17.", "kind": "event", "subject": "Robin", "predicate": "conference location", "value": "Lisbon", "topic": "events", "valid_from": "2026-10-10", "valid_until": "2026-10-18"},
        {"key": "luna", "content": "Luna expedition departure is scheduled for December 1.", "kind": "event", "subject": "Luna", "predicate": "expedition departure", "value": "2026-12-01", "topic": "events", "valid_from": "2026-12-01"},
        {"key": "thermostat", "content": "The greenhouse thermostat target is 19 degrees Celsius.", "subject": "greenhouse", "predicate": "thermostat target", "value": "19 Celsius", "topic": "home", "valid_from": "2026-01-01"},
        {"key": "build_os", "content": "Atlas build runners use Linux.", "subject": "Atlas", "predicate": "build operating system", "value": "Linux", "topic": "projects/atlas", "valid_from": "2026-02-01"},
        {"key": "nimbus_region", "content": "Nimbus deployment region is Europe.", "subject": "Nimbus", "predicate": "deployment region", "value": "Europe", "topic": "projects/nimbus", "valid_from": "2026-06-01"},
        {"key": "nimbus_language", "content": "Nimbus interface language is Spanish.", "subject": "Nimbus", "predicate": "interface language", "value": "Spanish", "topic": "projects/nimbus", "valid_from": "2026-06-01"},
        {"key": "backup", "content": "For backups, encrypt the archive before copying it to offline storage.", "kind": "procedure", "subject": "backup", "predicate": "archive procedure", "value": "encrypt before copying", "topic": "procedures", "valid_from": "2026-01-01"},
        {"key": "saffron", "content": "Saffron bicycle frame color is amber.", "subject": "Saffron", "predicate": "frame color", "value": "amber", "topic": "hobbies", "valid_from": "2026-10-20"},
    ]
    for memory in memories:
        memory.setdefault("kind", "fact")
        memory.setdefault("confidence", 0.9)
        memory.setdefault("importance", 0.5)
        memory.setdefault("source", "Synthetic benchmark fixture")
    # Validity is [valid_from, valid_until): boundary cases are intentional.
    cases = [
        {"id": "atlas_early", "query": "Atlas release status", "as_of": "2026-09-05", "expected": ["atlas_pending"], "category": "historical"},
        {"id": "atlas_boundary", "query": "Atlas release status", "as_of": "2026-09-15", "expected": ["atlas_beta"], "category": "boundary"},
        {"id": "atlas_mid", "query": "Atlas release status", "as_of": "2026-09-25", "expected": ["atlas_beta"], "category": "historical"},
        {"id": "atlas_current", "query": "Atlas release status", "expected": ["atlas_shipped"], "category": "updated"},
        {"id": "lyra_early", "query": "Lyra funding stage", "as_of": "2026-08-01", "expected": ["lyra_seed"], "category": "historical"},
        {"id": "lyra_current", "query": "Lyra funding stage", "expected": ["lyra_series_a"], "category": "updated"},
        {"id": "language_early", "query": "Robin preferred programming language", "as_of": "2026-09-01", "expected": ["language_python"], "category": "historical"},
        {"id": "language_current", "query": "Robin preferred programming language", "expected": ["language_rust"], "category": "updated"},
        {"id": "coffee_exact", "query": "black coffee", "expected": ["coffee"], "category": "exact"},
        {"id": "lisbon_during", "query": "Lisbon conference", "as_of": "2026-10-12", "expected": ["lisbon_trip"], "category": "event"},
        {"id": "lisbon_expired", "query": "Lisbon conference", "expected": [], "category": "expired"},
        {"id": "luna_not_yet", "query": "Luna expedition departure", "expected": [], "category": "future_exclusion"},
        {"id": "luna_valid", "query": "Luna expedition departure", "as_of": "2026-12-02", "expected": ["luna"], "category": "event"},
        {"id": "thermostat_exact", "query": "greenhouse thermostat target", "expected": ["thermostat"], "category": "exact"},
        {"id": "build_distractor", "query": "Atlas build runners Linux", "expected": ["build_os"], "category": "distractor"},
        {"id": "nimbus_related", "query": "Nimbus", "expected": ["nimbus_region", "nimbus_language"], "category": "multiple_facts"},
        {"id": "backup_procedure", "query": "encrypt archive backups", "expected": ["backup"], "category": "procedure"},
        {"id": "saffron_exact", "query": "Saffron bicycle frame color", "expected": ["saffron"], "category": "exact"},
        {"id": "unknown_entity", "query": "quasar violin sonata", "expected": [], "category": "unknown"},
        {"id": "unknown_fact", "query": "penguin passport number", "expected": [], "category": "unknown"},
        {"id": "build_paraphrase", "query": "Which computer platform runs compilation jobs?", "expected": ["build_os"], "category": "semantic_gap"},
        {"id": "backup_paraphrase", "query": "How should I secure recovery copies?", "expected": ["backup"], "category": "semantic_gap"},
    ]
    for case in cases:
        case.setdefault("as_of", "2026-10-22")
    return {"version": 1, "synthetic": True, "memories": memories, "cases": cases}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


def _naive_search(memories: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    """A time-blind token-overlap baseline; tie-breaking favors later facts."""
    terms = _tokens(query)
    scored = []
    for memory in memories:
        fields = " ".join(str(memory.get(field) or "") for field in ("content", "subject", "predicate", "value"))
        score = len(terms & _tokens(fields)) / max(1, len(terms))
        if score:
            scored.append((score, memory["valid_from"], memory["id"], memory))
    scored.sort(key=lambda item: item[:3], reverse=True)
    return [item[3] for item in scored[:limit]]


def _is_visible(memory: dict[str, Any], as_of: str) -> bool:
    point = normalize_time(as_of)
    return memory["status"] == "active" and memory["valid_from"] <= point and (
        memory.get("valid_until") is None or point < memory["valid_until"]
    )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _evolution_check() -> dict[str, Any]:
    """Check a single duplicate merge, including evidence retention and undo."""
    from .evolution import EvolutionEngine

    with tempfile.TemporaryDirectory(prefix="memory-evolution-check-") as directory:
        store = Store(directory)
        fields = {"content": "Robin prefers black coffee.", "kind": "preference", "subject": "Robin", "predicate": "coffee preference", "value": "black coffee", "topic": "profile/preferences", "valid_from": "2026-01-01"}
        first = store.add_memory(**fields, source="Synthetic conversation A")
        second = store.add_memory(**fields, source="Synthetic conversation B")
        engine = EvolutionEngine(store)
        proposals = engine.review()
        duplicate = next((item for item in proposals if item["kind"] in {"duplicate", "deduplicate", "deduplication"} and item.get("changes")), None)
        before = len(store.list_memories(as_of="2026-10-22"))
        if duplicate is None:
            return {"scenario": "Exact duplicate consolidation", "before_active": before, "applied": False, "reason": "No actionable duplicate proposal was produced."}
        engine.apply(duplicate["id"])
        remaining = store.list_memories(as_of="2026-10-22")
        all_records = store.list_memories(include_inactive=True)
        evidence_retained = all(store.get_source(item["source_id"]) for item in (first, second))
        meaning_preserved = bool(remaining) and all(item["content"] == fields["content"] for item in remaining)
        engine.undo(duplicate["id"])
        return {
            "scenario": "Exact duplicate consolidation",
            "before_active": before,
            "after_active": len(remaining),
            "records_retained": len(all_records),
            "evidence_retained": bool(evidence_retained),
            "meaning_preserved": meaning_preserved,
            "undo_restored_count": len(store.list_memories(as_of="2026-10-22")),
            "applied": True,
        }


def run_benchmark() -> dict[str, Any]:
    """Evaluate toy retrieval fixtures in an isolated temporary database."""
    dataset = synthetic_dataset()
    strategy_specs = [
        ("hybrid", "Hybrid · temporal", "Lexical + feature-hash similarity + metadata; validity filter enabled."),
        ("lexical", "Lexical · temporal", "Lexical scoring with the same validity filter."),
        ("vector", "Feature hash · temporal", "Deterministic local feature-hash cosine; not learned embeddings."),
        ("full_context", "Recent context · temporal", "Query-independent recent memory, truncated to the same three-record budget."),
        ("naive_keyword", "Keyword · time-blind", "Token overlap over all records, including past/future values; newer facts win ties."),
    ]
    with tempfile.TemporaryDirectory(prefix="memory-benchmark-") as directory:
        store = Store(directory)
        key_by_id = {}
        memories = []
        for fixture in dataset["memories"]:
            fields = dict(fixture)
            key = fields.pop("key")
            memory = store.add_memory(**fields)
            memories.append(memory)
            key_by_id[memory["id"]] = key
        retriever = Retriever(store)
        strategies = []
        case_results = []
        for strategy, name, description in strategy_specs:
            recalls, top_ones, abstentions, latencies, token_counts = [], [], [], [], []
            returned_count = stale_count = 0
            for case in dataset["cases"]:
                start = perf_counter()
                if strategy == "naive_keyword":
                    returned = _naive_search(memories, case["query"], 3)
                else:
                    result = retriever.search(case["query"], as_of=case["as_of"], limit=3, strategy=strategy, record=False)
                    returned = [item["memory"] for item in result["results"]]
                elapsed = (perf_counter() - start) * 1000
                labels = [key_by_id[item["id"]] for item in returned]
                expected = set(case["expected"])
                if expected:
                    recalls.append(len(expected & set(labels)) / len(expected))
                    top_ones.append(float(bool(labels) and labels[0] in expected))
                else:
                    abstentions.append(float(not labels))
                stale = sum(not _is_visible(item, case["as_of"]) for item in returned)
                returned_count += len(returned)
                stale_count += stale
                latencies.append(elapsed)
                # Estimate text-only context: metadata and model tokenization are excluded.
                token_counts.append(sum(math.ceil(len(item["content"]) / 4) for item in returned))
                case_results.append({"strategy": strategy, "case_id": case["id"], "category": case["category"], "query": case["query"], "as_of": case["as_of"], "expected": case["expected"], "returned": labels, "stale_hits": stale, "latency_ms": round(elapsed, 4)})
            strategies.append({
                "id": strategy, "name": name, "description": description,
                "recall_at_3": round(_mean(recalls), 4),
                "top1_accuracy": round(_mean(top_ones), 4),
                "abstention_accuracy": round(_mean(abstentions), 4),
                "stale_hit_rate": round(stale_count / returned_count, 4) if returned_count else 0.0,
                "mean_latency_ms": round(_mean(latencies), 4),
                "p95_latency_ms": round(sorted(latencies)[math.ceil(0.95 * len(latencies)) - 1], 4),
                "mean_context_tokens": round(_mean(token_counts), 2),
                "answerable_cases": len(recalls), "unanswerable_cases": len(abstentions),
                "returned_records": returned_count, "stale_records": stale_count,
            })
    evolution = _evolution_check()
    return {
        "title": "Synthetic memory regression benchmark",
        "summary": {"case_count": len(dataset["cases"]), "memory_count": len(dataset["memories"]), "strategy_count": len(strategy_specs), "context_budget_records": 3, "synthetic": True, "description": "22 hand-authored cases, including two paraphrases with no useful lexical overlap. Retrieval correctness only; no LLM, semantic embedding model, or external benchmark."},
        "strategies": strategies,
        "cases": case_results,
        "evolution": evolution,
        "methodology": [
            "Recall@3 and top-1 accuracy are averaged over answerable cases only; abstention accuracy uses unanswerable cases only.",
            "Stale hit rate is temporally invalid returned records divided by all returned records; validity intervals are start-inclusive and end-exclusive.",
            "All temporal strategies use the same validity filter. The time-blind baseline intentionally has no validity filter, so comparisons combine retrieval and temporal policy differences.",
            "Recent context is a truncated three-record baseline, not an unlimited full-context model.",
            "Token estimates are content characters divided by four, rounded up per record; they are not tokenizer measurements or API cost estimates.",
            "Latency is a single in-process measurement per case on this machine, excludes ingestion, and is not a load test.",
            "This tiny hand-authored regression set does not establish real-world performance, statistical significance, or superiority over RAG systems.",
            "Two semantic-gap cases intentionally require paraphrase understanding beyond lexical matching; the local feature-hash model is expected to miss them.",
        ],
    }


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))


_CHART_PANELS = (
    ("recall_at_3", "Recall@3", "higher is better"),
    ("top1_accuracy", "Top-1 accuracy", "higher is better"),
    ("abstention_accuracy", "Unknown-query abstention", "higher is better"),
    ("stale_hit_rate", "Invalid-time hits", "lower is better"),
)


def _escape(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_svg(result: dict[str, Any]) -> str:
    """Render the strategy comparison as a dependency-free SVG chart.

    Four small multiples share one 0–100% scale; one hue is used because each
    panel compares one measure across strategies, and strategy names label rows.
    """
    strategies = result["strategies"]
    label_width, bar_width, row, gap = 190, 150, 26, 36
    panel_width = bar_width + 56
    top, header = 64, 44
    width = label_width + len(_CHART_PANELS) * panel_width + 24
    height = top + header + row * len(strategies) + 64
    ink, muted, line, bar, surface = "#24392f", "#6b7a70", "#e3e7df", "#294e3c", "#ffffff"
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'font-family="-apple-system, BlinkMacSystemFont, \'Segoe UI\', Helvetica, Arial, sans-serif" role="img" '
        f'aria-labelledby="title desc">',
        '<title id="title">Synthetic retrieval benchmark by strategy</title>',
        f'<desc id="desc">{_escape(result["summary"]["description"])}</desc>',
        f'<rect width="{width}" height="{height}" rx="12" fill="{surface}"/>',
        f'<text x="24" y="34" font-size="17" font-weight="600" fill="{ink}">Retrieval strategies on the synthetic workload</text>',
        f'<text x="24" y="54" font-size="12" fill="{muted}">{result["summary"]["case_count"]} hand-authored queries · '
        f'{result["summary"]["memory_count"]} memories · three-record budget</text>',
    ]
    for index, (key, title, direction) in enumerate(_CHART_PANELS):
        x0 = label_width + index * panel_width
        out.append(f'<text x="{x0}" y="{top + 16}" font-size="12.5" font-weight="600" fill="{ink}">{_escape(title)}</text>')
        out.append(f'<text x="{x0}" y="{top + 31}" font-size="11" fill="{muted}">{direction}</text>')
        base_y = top + header
        for tick in (0, 0.5, 1.0):
            tx = x0 + tick * bar_width
            out.append(f'<line x1="{tx:.1f}" y1="{base_y - 4}" x2="{tx:.1f}" y2="{base_y + row * len(strategies)}" stroke="{line}" stroke-width="1"/>')
            out.append(f'<text x="{tx:.1f}" y="{base_y + row * len(strategies) + 16}" font-size="10" fill="{muted}" text-anchor="middle">{int(tick * 100)}%</text>')
        for position, strategy in enumerate(strategies):
            value = float(strategy[key])
            y = base_y + position * row + 6
            w = max(0.0, value * bar_width)
            if w > 0:
                out.append(
                    f'<rect x="{x0}" y="{y}" width="{w:.1f}" height="{row - 12}" rx="3" fill="{bar}">'
                    f'<title>{_escape(strategy["name"])}: {value:.1%} {_escape(title.lower())}</title></rect>'
                )
            out.append(f'<text x="{x0 + w + 6:.1f}" y="{y + row - 15}" font-size="11" fill="{ink}">{value:.0%}</text>')
    for position, strategy in enumerate(strategies):
        y = top + header + position * row + row - 9
        out.append(f'<text x="24" y="{y}" font-size="12" fill="{ink}">{_escape(strategy["name"])}</text>')
    foot = height - 18
    out.append(f'<text x="24" y="{foot}" font-size="10.5" fill="{muted}">Small deterministic regression fixtures, not a general quality claim. '
               'Recall and top-1 use 18 answerable queries; abstention uses 4 unanswerable ones.</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
