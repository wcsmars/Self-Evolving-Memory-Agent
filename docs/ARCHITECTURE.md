# Design notes

## Evidence and state

A source is immutable raw evidence with its own ingestion timestamp. A memory is an interpreted record pointing at one source. Editing a memory records its before/after state in an append-only audit table; it never rewrites that source. SQL triggers reject source and audit updates/deletes through ordinary SQLite operations. These are application integrity guarantees, not tamper-proof storage against someone controlling the database file.

SQLite transactions are the boundary for authoritative mutations. Writers take the store's in-process lock before the SQLite write lock, in the same order everywhere, so threads sharing one store cannot stall each other. WAL and busy timeouts support local readers and writers. Each memory has an integer version. Batch mutation validates every record and expected version before changing any of them. The Markdown view is generated after commit, so it is recoverable with `export`; files are not part of a cross-filesystem transaction. Unchanged files are not rewritten, and a path that cannot be written (for example a symlinked folder) is reported in `projection_errors` without stopping the rest of the export.

## Proposal lifecycle

A review inspects stored records and persists a deterministic proposal containing the rule, rationale, risk, before-images, expected versions, intended changes, and all source IDs used as evidence. Reviewing unchanged records does not repeatedly create the same proposal. A rejection is remembered for those versions.

Application is explicit. Its version checks include both changed records and supporting records (for example, the duplicate keeper). Memory mutations and the proposal's transition to applied share one transaction. Undo restores the previous value of each field the proposal wrote, provided every one of those fields still holds the value the proposal set. A later edit to the same field blocks undo, because undo would overwrite it. Edits to other fields, edits to supporting records, and later proposals that were themselves undone do not block it, so proposals can be undone in reverse order. Undo increments versions and leaves an audit record; it does not erase history. A proposal stays pending only while a fresh review would make exactly the same proposal. When its records are edited or new evidence joins its group, the next review marks it `outdated`: it remains in the history but cannot be applied, and an updated proposal takes its place. If the same records and versions recur, for example after an edit is undone, the original proposal returns to pending. Rejected proposals stay rejected.

Closing an older fact at a newer fact's start is an interpretation of chronology. It can be wrong when different sources disagree or when a correction is retroactive. The proposal explains that hypothesis and requires a human decision. Same-start conflicts have no applicable automatic transformation. Neither does an interruption, where a newer value ends before an older open value: shortening the older interval would erase the period after the interruption, and one interval cannot express the older value resuming. Deduplication declines paraphrases and records with different confidence, importance, or end dates. Structured facts and events must also share a start date, because a value can legitimately return later. An open-ended note, preference or procedure that is repeated later (for example by re-importing a conversation) is a duplicate of the earliest copy, which already covers every later date. A rejected deduplication does not hide conflicts: the copies then receive identical timeline updates.

## Retrieval and learning

The ranker first filters records by status and validity, then computes BM25-like lexical relevance, hashed word/character cosine, recency relative to the query date, importance, confidence, and learned usefulness. Rank signals are returned with each result. Candidates must share a word with what the memory states (content, subject, predicate or value). Topic and kind labels help ranking but cannot qualify a record on their own, so a query such as "unknown facts" abstains. This prevents arbitrary hash collisions from supplying unrelated evidence but sacrifices synonym-only recall. Tokens are case-folded with a small explicit plural normalizer. Chinese and Japanese text, written without spaces, is indexed as overlapping character pairs.

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
