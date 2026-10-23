# Local API

Start with `python3 run.py serve`. Base URL: `http://127.0.0.1:8765`.
POST requests require `Content-Type: application/json` and a JSON object. Bodies are limited to 1 MiB. Errors return `{"error":"message"}`. Foreign browser origins and non-loopback Host headers are rejected. Every GET route also answers `HEAD` with the same headers and no body.

| Method | Path | Input / output |
| --- | --- | --- |
| GET | `/api/health` | Version and health. |
| GET | `/api/overview` | Statistics, learned relevance state, proposals, recent audit. |
| GET | `/api/memories` | `{memories:[...]}`; filters `include_inactive=true`, `as_of`, `topic`, `kind`. |
| POST | `/api/memories` | Add one memory; returns `{memory:...}`. |
| POST | `/api/memories/{id}/update` | `{expected_version:1,changes:{...}}`; returns `{memory:...}`. |
| POST | `/api/ingest` | `{text,source?,topic?}`; returns `{memories:[...]}`. |
| POST | `/api/search` | `{query,as_of?,limit?}`; returns ranked memories, scores, signals, retrieval ID. |
| POST | `/api/feedback` | `{retrieval_id,relevant_ids:[...]}`; updates learned relevance. |
| POST | `/api/review` | `{}`; creates review proposals without applying them. |
| GET | `/api/proposals` | Optional `status`; returns `{proposals:[...]}`. |
| POST | `/api/proposals/{id}/apply` | `{}`; checks versions and applies the complete proposal atomically. |
| POST | `/api/proposals/{id}/reject` | `{}`; records rejection. |
| POST | `/api/proposals/{id}/undo` | `{}`; restores previous semantic fields if versions are unchanged. |
| GET | `/api/sources/{id}` | Original source text, label, identifier, ingestion time. |
| GET | `/api/events` | `{events:[...]}`; append-only audit, newest first. |
| POST | `/api/demo` | Seeds only an empty store, reviews it, returns overview. |
| POST | `/api/benchmark` | Isolated synthetic regression report. |

Add example:

```bash
curl http://127.0.0.1:8765/api/memories \
  -H 'Content-Type: application/json' \
  -d '{"content":"Atlas uses SQLite","kind":"fact","topic":"projects/atlas","subject":"Atlas","predicate":"database","value":"SQLite","valid_from":"2026-01-01","source":"Architecture note"}'
```

Memory inputs: `content` (required), `kind`, `topic`, `subject`, `predicate`, `value`, `valid_from`, `valid_until`, `confidence`, `importance`, `source`, `source_text`. Structured fields must be all present or all absent. Scores are finite values in `[0,1]`. Omit optional fields rather than submitting empty strings. Topics use safe slash-separated identifiers with up to five components.

Updates accept only mutable semantic fields plus `status` (`active`/`archived`). Evidence, IDs and creation metadata cannot be rewritten. Include the version from the most recent read; stale edits return HTTP 409. Proposal-state errors are reported as validation errors; reload before retrying.

Feedback labels the **whole retrieved result set**: IDs in `relevant_ids` are positive, all other returned IDs are negative. Unknown IDs are rejected. An identical retry is idempotent; a conflicting second feedback submission is rejected. Feedback adjusts a per-memory Beta relevance estimate, not source text or the fixed feature weights.

Do not expose this unauthenticated development API to the internet. Authentication and a production application server are outside this release's scope.

