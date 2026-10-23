"""Transparent local retrieval with a persistent, feedback-trained relevance prior.

This module deliberately does not claim to provide semantic embeddings. Its vector
baseline hashes word and character features into a fixed-size vector. Candidate
selection requires a meaningful word overlap, so unrelated character collisions
cannot turn into apparently supported answers. Retrieval scans the visible corpus;
it is intended for personal memory collections, not a large vector database.
"""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import re
from typing import Any, Dict, List, Optional
import uuid

from .store import normalize_time, utcnow


_STOPWORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had "
    "has have how i if in into is it its me my of on or our please should so "
    "tell than that the their them then there these they this those to us was "
    "we were what when where which who why will with would you your about "
    "any all know show give find recall remember memory memories".split()
)
_WEIGHTS = {
    "lexical": 0.46,
    "vector": 0.18,
    "recency": 0.08,
    "importance": 0.07,
    "confidence": 0.06,
    "usefulness": 0.15,
}
_STRATEGIES = frozenset({"hybrid", "lexical", "vector", "full_context"})


def _tokens(text: str) -> List[str]:
    words = re.findall(r"[^\W_]+", text.casefold(), flags=re.UNICODE)
    result = []
    for word in words:
        if word in _STOPWORDS:
            continue
        # A small, explicit plural normalizer, not an opaque language model.
        if len(word) > 5 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us")):
            word = word[:-1]
        result.append(word)
    return result


def _memory_text(memory: Dict[str, Any]) -> str:
    return " ".join(
        str(memory.get(key) or "")
        for key in ("content", "subject", "predicate", "value", "topic", "kind")
    )


def _vector(tokens: List[str], dimensions: int = 1024) -> Dict[int, float]:
    features = Counter()
    for token in tokens:
        features["word:" + token] += 2.0
        padded = "^" + token + "$"
        for position in range(max(0, len(padded) - 2)):
            features["char:" + padded[position:position + 3]] += 0.2
    result: Dict[int, float] = {}
    for feature, count in features.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        slot = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1 if digest[4] & 1 else -1
        result[slot] = result.get(slot, 0.0) + sign * count
    length = math.sqrt(sum(value * value for value in result.values()))
    if length:
        result = {slot: value / length for slot, value in result.items()}
    return result


def _cosine(left: Dict[int, float], right: Dict[int, float]) -> float:
    return max(0.0, min(1.0, sum(value * right.get(slot, 0.0) for slot, value in left.items())))


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class Retriever:
    """Retrieve visible facts and learn which retrieved records were useful.

    Feedback is a complete relevance label for one result set: checked records
    are positive and unchecked records are negative. A Beta(1, 1) prior keeps
    early judgments bounded. Each retrieval accepts feedback once; an identical
    retry is idempotent. Source text and memory meaning are never changed.
    """

    def __init__(self, store: Any):
        self.store = store
        with closing(self.store.connection()) as connection:
            with connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS retrieval_traces (
                        id TEXT PRIMARY KEY,
                        query TEXT NOT NULL,
                        as_of TEXT,
                        strategy TEXT NOT NULL,
                        result_ids TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS retrieval_feedback (
                        retrieval_id TEXT PRIMARY KEY REFERENCES retrieval_traces(id),
                        relevant_ids TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS memory_usefulness (
                        memory_id TEXT PRIMARY KEY,
                        positive INTEGER NOT NULL DEFAULT 0,
                        negative INTEGER NOT NULL DEFAULT 0
                    );
                """)

    def learning_state(self) -> Dict[str, Any]:
        with closing(self.store.connection()) as connection:
            feedback_count = connection.execute("SELECT COUNT(*) FROM retrieval_feedback").fetchone()[0]
            learned_memories = connection.execute("SELECT COUNT(*) FROM memory_usefulness").fetchone()[0]
        return {
            "feedback_count": feedback_count,
            "learned_memories": learned_memories,
            "weights": dict(_WEIGHTS),
            "method": "Per-memory Beta(1, 1) relevance prior; fixed feature weights",
            "vector_model": "Local 1024-dimensional hashed word and character features (not semantic embeddings)",
        }

    def search(
        self,
        query: str,
        as_of: Optional[str] = None,
        limit: int = 8,
        strategy: str = "hybrid",
        record: bool = True,
    ) -> Dict[str, Any]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be a non-empty string.")
        query = query.strip()
        if len(query) > 4000:
            raise ValueError("Query must be at most 4,000 characters.")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Limit must be an integer from 1 to 100.")
        if strategy not in _STRATEGIES:
            raise ValueError("Unknown retrieval strategy: %s" % strategy)
        if as_of is not None and not isinstance(as_of, str):
            raise ValueError("as_of must be a date or ISO timestamp.")
        normalized_as_of = normalize_time(as_of) if as_of else None
        reference_time = _datetime(normalized_as_of or utcnow())
        memories = self.store.list_memories(as_of=normalized_as_of)
        query_tokens = _tokens(query)
        query_terms = set(query_tokens)
        token_lists = [_tokens(_memory_text(memory)) for memory in memories]
        counts = [Counter(tokens) for tokens in token_lists]
        average_length = sum(len(tokens) for tokens in token_lists) / max(1, len(token_lists))
        document_frequency = Counter()
        for tokens in token_lists:
            document_frequency.update(set(tokens))
        query_vector = _vector(query_tokens)
        with closing(self.store.connection()) as connection:
            usefulness = {
                row["memory_id"]: (row["positive"] + 1) / (row["positive"] + row["negative"] + 2)
                for row in connection.execute("SELECT * FROM memory_usefulness")
            }

        results = []
        for memory, tokens, term_counts in zip(memories, token_lists, counts):
            overlap = query_terms.intersection(term_counts)
            if strategy != "full_context" and not overlap:
                continue
            raw_bm25 = 0.0
            for term in overlap:
                frequency = term_counts[term]
                inverse_frequency = math.log(1.0 + (len(memories) - document_frequency[term] + 0.5)
                                             / (document_frequency[term] + 0.5))
                denominator = frequency + 1.5 * (0.25 + 0.75 * len(tokens) / max(average_length, 1))
                raw_bm25 += inverse_frequency * frequency * 2.5 / denominator
            lexical = 1.0 - math.exp(-raw_bm25)
            vector = _cosine(query_vector, _vector(tokens))
            age_days = max(0.0, (reference_time - _datetime(memory["valid_from"])).total_seconds() / 86400)
            recency = math.exp(-math.log(2) * age_days / 180)
            signals = {
                "lexical": lexical,
                "vector": vector,
                "recency": recency,
                "importance": float(memory["importance"]),
                "confidence": float(memory["confidence"]),
                "usefulness": usefulness.get(memory["id"], 0.5),
                "query_coverage": len(overlap) / max(1, len(query_terms)),
                "bm25": raw_bm25,
            }
            if strategy == "lexical":
                score = lexical
            elif strategy == "vector":
                score = vector
            elif strategy == "full_context":
                score = recency
            else:
                score = sum(signals[key] * weight for key, weight in _WEIGHTS.items())
            results.append({
                "memory": memory,
                "score": round(score, 8),
                "signals": {key: round(value, 8) for key, value in signals.items()},
            })

        if strategy == "full_context":
            results.sort(key=lambda item: (item["memory"]["valid_from"], item["memory"]["id"]), reverse=True)
        else:
            results.sort(key=lambda item: (-item["score"], item["memory"]["id"]))
        results = results[:limit]
        retrieval_id = None
        if record:
            retrieval_id = "r_" + uuid.uuid4().hex
            with closing(self.store.connection()) as connection:
                with connection:
                    connection.execute(
                        "INSERT INTO retrieval_traces(id,query,as_of,strategy,result_ids,created_at) VALUES(?,?,?,?,?,?)",
                        (retrieval_id, query, normalized_as_of, strategy,
                         json.dumps([item["memory"]["id"] for item in results]), utcnow()),
                    )
        return {
            "query": query,
            "as_of": normalized_as_of,
            "strategy": strategy,
            "results": results,
            "retrieval_id": retrieval_id,
        }

    def feedback(self, retrieval_id: str, relevant_ids: List[str]) -> Dict[str, Any]:
        if not isinstance(retrieval_id, str) or not retrieval_id:
            raise ValueError("A retrieval_id is required.")
        if not isinstance(relevant_ids, list) or not all(isinstance(value, str) for value in relevant_ids):
            raise ValueError("relevant_ids must be a list of memory IDs.")
        canonical_ids = sorted(set(relevant_ids))
        encoded_ids = json.dumps(canonical_ids)
        with closing(self.store.connection()) as connection:
            with connection:
                # Serialize read/check/write so concurrent retries cannot double count.
                connection.execute("BEGIN IMMEDIATE")
                trace = connection.execute("SELECT result_ids FROM retrieval_traces WHERE id=?", (retrieval_id,)).fetchone()
                if trace is None:
                    raise KeyError("Retrieval not found: %s" % retrieval_id)
                result_ids = json.loads(trace["result_ids"])
                if not result_ids:
                    raise ValueError("An empty retrieval has no memories to rate.")
                if not set(canonical_ids).issubset(result_ids):
                    raise ValueError("Feedback may only reference memories shown in this retrieval.")
                existing = connection.execute(
                    "SELECT relevant_ids FROM retrieval_feedback WHERE retrieval_id=?", (retrieval_id,)
                ).fetchone()
                if existing is not None:
                    if existing["relevant_ids"] != encoded_ids:
                        raise ValueError("Feedback was already recorded for this retrieval. Run a new search to rate again.")
                else:
                    connection.execute(
                        "INSERT INTO retrieval_feedback(retrieval_id,relevant_ids,created_at) VALUES(?,?,?)",
                        (retrieval_id, encoded_ids, utcnow()),
                    )
                    relevant = set(canonical_ids)
                    for memory_id in result_ids:
                        positive = int(memory_id in relevant)
                        connection.execute(
                            "INSERT INTO memory_usefulness(memory_id,positive,negative) VALUES(?,?,?) "
                            "ON CONFLICT(memory_id) DO UPDATE SET "
                            "positive=positive+excluded.positive,negative=negative+excluded.negative",
                            (memory_id, positive, 1 - positive),
                        )
        return self.learning_state()
