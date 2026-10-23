"""Conservative offline extraction; ambiguous prose stays verbatim as a note."""
import re
from typing import Optional

from .store import Store, normalize_time


def extract(text: str, topic: str = "inbox") -> list:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text must be a non-empty string")
    if len(text) > 100_000:
        raise ValueError("text is limited to 100,000 characters per import")
    items = []
    for raw in text.splitlines():
        line = re.sub(r"^\s*[-*]\s+", "", raw).strip()
        if not line or line.startswith("#"):
            continue
        content = line
        date = re.match(r"^\[(\d{4}-\d{2}-\d{2})\]\s*(.+)$", line)
        valid_from: Optional[str] = None
        if date:
            valid_from = normalize_time(date.group(1))
            line = date.group(2)
        item = dict(content=content, kind="note", topic=topic, confidence=0.75)
        if valid_from:
            item["valid_from"] = valid_from
        parts = [p.strip() for p in line.split("|")]
        if len(parts) == 3 and all(parts):
            item.update(kind="fact", subject=parts[0], predicate=parts[1], value=parts[2], confidence=0.95)
        elif re.match(r"^(?:I|We) prefer\b", line, re.I):
            item.update(kind="preference", confidence=0.9)
        elif re.match(r"^(?:Always|Never)\s", line, re.I):
            item.update(kind="procedure", confidence=0.85)
        # No guessed entities, inferred dates, summaries, or invented facts.
        items.append(item)
    if not items:
        raise ValueError("No content lines were found")
    if len(items) > 200:
        raise ValueError("Import at most 200 content lines at a time")
    return items


def ingest(store: Store, text: str, source: str = "text import", topic: str = "inbox") -> list:
    items = extract(text, topic)
    return store.add_memories([{**item, "source": source, "source_text": text} for item in items])

