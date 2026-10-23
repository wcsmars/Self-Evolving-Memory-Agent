# Design notes

## Evidence and state

A source is immutable raw evidence with its own ingestion timestamp. A memory is an interpreted record pointing at one source. Editing a memory records its before/after state in an append-only audit table; it never rewrites that source. SQL triggers reject source and audit updates/deletes through ordinary SQLite operations. These are application integrity guarantees, not tamper-proof storage against someone controlling the database file.

SQLite transactions are the boundary for authoritative mutations. WAL and busy timeouts support local readers and writers. Each memory has an integer version. Batch mutation validates every record and expected version before changing any of them. The Markdown view is generated after commit, so it is recoverable with `export`; files are not part of a cross-filesystem transaction.

## Proposal lifecycle

A review inspects stored records and persists a deterministic proposal containing the rule, rationale, risk, before-images, expected versions, intended changes, and all source IDs used as evidence. Reviewing unchanged records does not repeatedly create the same proposal. A rejection is remembered for those versions.

Application is explicit. Its version checks include both changed records and supporting records (for example, the duplicate keeper). Memory mutations and the proposal's transition to applied share one transaction. Undo requires the post-application versions to still match before restoring the previous semantic fields. Undo increments versions and leaves an audit record; it does not erase history. Existing stale pending proposals remain visible and can be rejected; a new review can produce updated proposals.

Closing an older fact at a newer fact's start is an interpretation of chronology. It can be wrong when different sources disagree or when a correction is retroactive. The proposal explains that hypothesis and requires a human decision. Same-start conflicts have no applicable automatic transformation. Exact deduplication intentionally declines paraphrases and records with different confidence, importance, or validity intervals.

## Retrieval and learning

The ranker first filters records by status and validity, then computes BM25-like lexical relevance, hashed word/character cosine, recency relative to the query date, importance, confidence, and learned usefulness. Rank signals are returned with each result. Candidates need a meaningful token overlap; this prevents arbitrary hash collisions from supplying unrelated evidence but sacrifices synonym-only recall.

Feedback is a binary relevance judgment for one result set. A Beta(1,1) prior yields `(positive + 1)/(positive + negative + 2)` for each rated memory. Hybrid retrieval gives this bounded estimate a fixed weight. Feedback is persisted once per retrieval. This is an inspectable online relevance update, not training an embedding model or optimizing a global routing policy.

## Scope boundaries

- Valid-time queries use the current corrected timeline. There is no separate knowledge-time query API.
- Retrieval returns evidence and contradictions, without an LLM answer-generation layer.
- Prose extraction uses documented rules; ambiguous language remains a note.
- Original evidence is retained. Archiving is not deletion or secure erasure.
- Topic splitting changes the generated directory structure. It does not summarize or discard evidence to meet a token budget.
- Source text is treated as data. The application does not execute instructions found in imported content.
- The service is for one local user. It has no multi-tenant authorization, network deployment, MCP protocol adapter, or external synchronization.
- Benchmark results are deterministic synthetic retrieval checks, apart from measured timing and ties between equally scored records. They do not establish production-quality semantic memory.

## Testing priorities

Tests concentrate on failure modes that could silently damage memory: temporal boundary errors, future leakage, partial batch mutation, stale proposal application, undo overwriting later edits, lost evidence, out-of-scope feedback, unsafe topic paths, and incomplete import validation. API tests exercise request handling and a real temporary SQLite collection. Socket-based tests run where the host allows loopback binding; a transport substitute exercises the same HTTP handler in restricted environments.
