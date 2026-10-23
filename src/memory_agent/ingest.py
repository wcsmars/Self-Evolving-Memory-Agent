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
    # Split on real line breaks only; str.splitlines() also breaks on form
    # feeds and Unicode separators that can appear inside one pasted line.
    for raw in re.split(r"\r\n|\r|\n", text):
        line = re.sub(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+", "", raw).strip()
        # Skip Markdown headings ("# Title"), not text such as "#1 priority".
        if not line or re.match(r"#{1,6}(?:\s|$)", line):
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

