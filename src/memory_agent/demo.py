"""An intentionally imperfect, entirely synthetic memory collection."""
from .store import Store


def seed_demo(store: Store) -> dict:
    # Do not mix the example into an existing collection, or reseed on restart.
    if store.list_memories(include_inactive=True):
        return {"seeded": False, "reason": "The collection already contains memories."}
    data = [
        dict(content="Project Atlas uses SQLite for persistent memory.", kind="fact", topic="projects/atlas", subject="Project Atlas", predicate="database", value="SQLite", valid_from="2026-01-01", source="Architecture notes, January"),
        dict(content="Project Atlas uses SQLite for persistent memory.", kind="fact", topic="projects/atlas", subject="Project Atlas", predicate="database", value="SQLite", valid_from="2026-01-01", source="Team recap, January"),
        dict(content="Project Atlas status: prototype.", kind="fact", topic="projects/atlas", subject="Project Atlas", predicate="status", value="prototype", valid_from="2026-01-01", source="Kickoff notes"),
        dict(content="Project Atlas status: beta.", kind="fact", topic="projects/atlas", subject="Project Atlas", predicate="status", value="beta", valid_from="2026-02-01", source="February review"),
        dict(content="I prefer concise weekly research summaries with source links.", kind="note", topic="profile", valid_from="2026-01-05", source="Example conversation"),
        dict(content="Always preserve original evidence when consolidating a memory.", kind="procedure", topic="agent/rules", valid_from="2026-01-01", source="Example policy"),
        dict(content="The Orion design review took place on January 15.", kind="event", topic="projects/orion", valid_from="2026-01-15", valid_until="2026-01-16", source="Example meeting notes"),
        dict(content="Project Orion milestone: reliability review.", kind="fact", topic="projects/orion", subject="Project Orion", predicate="milestone", value="reliability review", valid_from="2026-02-03", source="Planning notes"),
        dict(content="Project Atlas owner: Taylor.", kind="fact", topic="projects/atlas", subject="Project Atlas", predicate="owner", value="Taylor", valid_from="2026-01-01", source="Example roster"),
        dict(content="Project Atlas owner: Morgan.", kind="fact", topic="projects/atlas", subject="Project Atlas", predicate="owner", value="Morgan", valid_from="2026-01-01", source="Conflicting example roster"),
    ]
    store.add_memories([{**item, "confidence": 0.9, "importance": 0.7} for item in data])
    return {"seeded": True, "count": len(data)}

