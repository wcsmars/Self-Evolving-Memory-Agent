"""Conservative, reviewable transformations of stored memory.

The agent evolves its organization, never its original evidence. Every proposal
contains versioned before-images, and applying or reverting it is a single
SQLite transaction. These deterministic rules are deliberately inspectable;
they are not a claim of autonomous language understanding.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing, contextmanager
from typing import Any, Dict, List, Optional

from .store import Store, utcnow


class ProposalError(ValueError):
    """An invalid transition, stale proposal, or unresolved contradiction."""


class EvolutionEngine:
    """Generate proposals and require explicit apply/reject/undo decisions."""

    def __init__(self, store: Store, topic_limit: int = 8):
        if not isinstance(topic_limit, int) or isinstance(topic_limit, bool) or topic_limit < 2:
            raise ValueError("topic_limit must be an integer of at least 2")
        self.store = store
        self.topic_limit = topic_limit
        with self._connection() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY,
                    fingerprint TEXT UNIQUE NOT NULL,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    changes_json TEXT NOT NULL,
                    before_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    after_versions_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    applied_at TEXT,
                    reverted_at TEXT
                )"""
            )

    @contextmanager
    def _connection(self):
        # sqlite3.Connection.__exit__ commits/rolls back but never closes.
        with closing(self.store.connection()) as conn:
            with conn:
                yield conn

    @staticmethod
    def _decode(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for column, public in (
            ("changes_json", "changes"),
            ("before_json", "before"),
            ("evidence_json", "evidence_ids"),
            ("after_versions_json", "after_versions"),
        ):
            result[public] = json.loads(result.pop(column))
        result.pop("fingerprint", None)
        result["actionable"] = any(change["changes"] for change in result["changes"])
        return result

    def get_proposal(self, proposal_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return self._decode(row)

    def list_proposals(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status is not None and status not in {"pending", "applied", "rejected", "reverted"}:
            raise ValueError("Unknown proposal status")
        sql = "SELECT * FROM proposals"
        args = ()
        if status is not None:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY created_at DESC, id"
        with self._connection() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._decode(row) for row in rows]

    def _propose(
        self,
        kind: str,
        title: str,
        reason: str,
        risk: str,
        memories: List[Dict[str, Any]],
        updates: Dict[str, Dict[str, Any]],
    ) -> None:
        ordered = sorted(memories, key=lambda item: item["id"])
        # Assertion-only entries protect supporting memories as well as changed
        # ones. A modified keeper must make a duplicate proposal stale.
        changes = [
            {"id": item["id"], "expected_version": item["version"], "changes": updates.get(item["id"], {})}
            for item in ordered
        ] if updates else []
        before = {item["id"]: item for item in ordered}
        evidence = sorted({item["source_id"] for item in ordered})
        fingerprint = hashlib.sha256(
            json.dumps(
                {"kind": kind, "changes": changes, "versions": [(m["id"], m["version"]) for m in ordered]},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        now = utcnow()
        with self._connection() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO proposals
                (id, fingerprint, kind, title, reason, risk, changes_json,
                 before_json, evidence_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("p_" + fingerprint[:24], fingerprint, kind, title, reason, risk,
                 json.dumps(changes, ensure_ascii=False), json.dumps(before, ensure_ascii=False),
                 json.dumps(evidence), now, now),
            )

    @staticmethod
    def _same_meaning_key(memory: Dict[str, Any]) -> tuple:
        # No embedding threshold or paraphrase inference: small wording changes
        # may change a fact. Source IDs differ but original sources remain stored.
        keys = (
            "content", "kind", "topic", "subject", "predicate", "value",
            "valid_from", "valid_until", "confidence", "importance",
        )
        return tuple(memory.get(key) for key in keys)

    @staticmethod
    def _overlap(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
        return (a["valid_until"] is None or b["valid_from"] < a["valid_until"]) and (
            b["valid_until"] is None or a["valid_from"] < b["valid_until"]
        )

    def review(self) -> List[Dict[str, Any]]:
        """Persist suggestions and return pending proposals, without applying any.

        Reviewing the same versions is idempotent, including after rejection.
        Existing stale proposals remain visible and will fail their version
        check if applied. A new version can produce a new proposal.
        """
        memories = [m for m in self.store.list_memories(include_inactive=True) if m["status"] == "active"]
        claimed = set()
        duplicates = defaultdict(list)
        for memory in memories:
            duplicates[self._same_meaning_key(memory)].append(memory)
        for group in duplicates.values():
            if len(group) < 2:
                continue
            group.sort(key=lambda m: (m["created_at"], m["id"]))
            keeper = group[0]
            self._propose(
                "deduplicate", "Consolidate {} identical memories".format(len(group)),
                "The content, structured fact, type, topic, confidence, importance and validity interval match exactly. "
                "Keep {} and archive the repeated records. Every original source and revision remains available.".format(keeper["id"]),
                "low", group, {m["id"]: {"status": "archived"} for m in group[1:]},
            )
            claimed.update(m["id"] for m in group)

        facts = defaultdict(list)
        for memory in memories:
            if all(memory.get(k) is not None for k in ("subject", "predicate", "value")):
                # Avoid silently equating distinct identifiers, e.g. ticker case.
                facts[(memory["subject"], memory["predicate"])].append(memory)
        for (subject, predicate), group in facts.items():
            # Resolve duplicates first, so no overlapping operation can hide
            # another copy of a fact from the proposed historical timeline.
            if any(memory["id"] in claimed for memory in group):
                continue
            group.sort(key=lambda m: (m["valid_from"], m["id"]))
            conflicts = [(a, b) for i, a in enumerate(group) for b in group[i + 1:]
                         if a["value"] != b["value"] and self._overlap(a, b)]
            if not conflicts:
                continue
            involved = {m["id"]: m for pair in conflicts for m in pair}
            simultaneous = any(a["valid_from"] == b["valid_from"] for a, b in conflicts)
            if simultaneous:
                self._propose(
                    "contradiction", "Review conflicting values for {} · {}".format(subject, predicate),
                    "Different values overlap and at least two begin at the same time. Chronology cannot choose a winner. "
                    "Inspect the original evidence and edit the fact or its validity interval manually; this proposal cannot be applied.",
                    "high", group, {},
                )
            else:
                updates = {}
                for older, newer in conflicts:
                    end = newer["valid_from"]
                    if older["id"] not in updates or end < updates[older["id"]]["valid_until"]:
                        updates[older["id"]] = {"valid_until": end}
                self._propose(
                    "supersede", "Separate changing values for {} · {}".format(subject, predicate),
                    "Different values have overlapping validity intervals. Proposed interpretation: the later dated value "
                    "supersedes the earlier value from its start time. This is a hypothesis requiring review, not a truth judgment. "
                    "Close the older interval while preserving the earlier fact for historical queries.",
                    "medium", group, updates,
                )
            claimed.update(m["id"] for m in group)

        for memory in memories:
            if memory["id"] in claimed or memory["kind"] != "note" or memory["valid_until"] is not None:
                continue
            # Only an explicit self-stated preference, never inferred sentiment.
            if re.match(r"^\s*I (?:prefer|always prefer)\s+\S", memory["content"], re.IGNORECASE):
                self._propose(
                    "classify", "Classify an explicit preference",
                    "This note explicitly begins with 'I prefer' and has no expiry. Reclassify its type while preserving its wording and evidence.",
                    "low", [memory], {memory["id"]: {"kind": "preference"}},
                )
                claimed.add(memory["id"])

        topics = defaultdict(list)
        for memory in memories:
            if memory["id"] not in claimed:
                topics[memory["topic"]].append(memory)
        for topic, group in topics.items():
            # Repeated subdivision by the same kind would create endless paths.
            if len(group) <= self.topic_limit or len(topic.split("/")) >= 4 or len(topic) + len("/preference") > 256:
                continue
            kinds = {m["kind"] for m in group}
            if len(kinds) < 2:
                continue
            updates = {m["id"]: {"topic": topic + "/" + m["kind"]}
                       for m in group if topic.split("/")[-1] != m["kind"]}
            if updates:
                self._propose(
                    "reorganize", "Split {} into memory types".format(topic),
                    "This topic contains {} records and multiple memory types. Move records into type subfolders to make the Markdown projection easier to browse. "
                    "Content, evidence and timestamps do not change.".format(len(group)),
                    "low", group, updates,
                )
        return self.list_proposals("pending")

    def _load_locked(self, conn: sqlite3.Connection, proposal_id: str, required_status: str) -> Dict[str, Any]:
        row = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        proposal = self._decode(row)
        if proposal["status"] != required_status:
            raise ProposalError("Proposal is {}; expected {}".format(proposal["status"], required_status))
        return proposal

    def apply(self, proposal_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = self._load_locked(conn, proposal_id, "pending")
            if not proposal["actionable"]:
                raise ProposalError("This contradiction requires manual evidence review and cannot be applied")
            if proposal["kind"] == "supersede":
                # Versions detect edits; this set check detects newly arriving
                # evidence that was not part of the reviewed hypothesis.
                sample = next(iter(proposal["before"].values()))
                current_ids = {row["id"] for row in conn.execute(
                    "SELECT id FROM memories WHERE status='active' AND subject=? AND predicate=?",
                    (sample["subject"], sample["predicate"]),
                )}
                if current_ids != set(proposal["before"]):
                    raise ProposalError("Relevant facts changed since review; reject this stale proposal and review again")
            after = self.store.mutate_memories(
                proposal["changes"], event_type="proposal_applied",
                event_data={"proposal_id": proposal_id, "evidence_ids": proposal["evidence_ids"]},
                connection=conn,
            )
            now = utcnow()
            conn.execute(
                "UPDATE proposals SET status='applied', applied_at=?, updated_at=?, after_versions_json=? WHERE id=?",
                (now, now, json.dumps({m["id"]: m["version"] for m in after}), proposal_id),
            )
        self.store.refresh_projections()
        return self.get_proposal(proposal_id)

    def reject(self, proposal_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._load_locked(conn, proposal_id, "pending")
            conn.execute("UPDATE proposals SET status='rejected', updated_at=? WHERE id=?", (utcnow(), proposal_id))
        return self.get_proposal(proposal_id)

    def undo(self, proposal_id: str) -> Dict[str, Any]:
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            proposal = self._load_locked(conn, proposal_id, "applied")
            restore = []
            for change in proposal["changes"]:
                memory_id = change["id"]
                restore.append({
                    "id": memory_id,
                    "expected_version": proposal["after_versions"][memory_id],
                    "changes": {key: proposal["before"][memory_id][key] for key in change["changes"]},
                })
            self.store.mutate_memories(
                restore, event_type="proposal_reverted",
                event_data={"proposal_id": proposal_id, "evidence_ids": proposal["evidence_ids"]},
                connection=conn,
            )
            now = utcnow()
            conn.execute(
                "UPDATE proposals SET status='reverted', reverted_at=?, updated_at=? WHERE id=?",
                (now, now, proposal_id),
            )
        self.store.refresh_projections()
        return self.get_proposal(proposal_id)
