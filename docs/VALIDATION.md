# Validation record

Validated on 23 October 2026 on macOS with Python 3.9.6 and Python 3.11 (tests), Python 3.11 (wheel build), Node 26 (JavaScript syntax), and Chrome (dashboard). GitHub Actions runs the tests on Python 3.9, 3.11 and 3.13.

- 92 automated tests passed on both local Python versions, including real-socket HTTP tests and the same scenarios through an in-memory transport.
- A fresh clone installed into a clean virtual environment and ran the CLI, the server and the dashboard assets from the installed package. Editable installs need pip 21.3 or newer, so the README upgrades pip first; macOS's built-in Python 3.9 ships an older pip.
- Every README command was run against a fresh collection: import, review, apply, undo, dated search, version-checked update, feedback, reject, watch, stats and export.
- A multi-process stress test (four writers, two reviewers applying proposals concurrently) kept all 160 writes, passed SQLite and full-text-index integrity checks, refused stale proposals instead of overwriting newer data, and left every timeline with exactly one open interval once writes stopped.
- The dashboard was exercised in a real browser against a fresh demo collection, at desktop and 375 px phone width. The screenshots in `docs/images/` come from that session.

### Issues found and fixed in this pass

A line-by-line review of the store, evolution engine, retrieval, server, CLI and dashboard found these defects. Each fix has a regression test.

- **Evolution.** A temporary value that ended while an earlier value was still open was treated as a replacement, which erased the earlier value after the interruption; it is now an "interruption" that needs manual review. A rejected duplicate proposal no longer hides conflicts for the same fact. Proposals whose records changed are marked `outdated` instead of staying pending, so a conflict resolved by hand disappears from the queue. Undo now checks only the fields it restores, so proposals can be undone in reverse order. Repeated open-ended notes, such as a re-imported conversation, are recognized as duplicates.
- **Retrieval.** Topic and kind labels alone no longer qualify a record, so queries such as "unknown facts" abstain. Plurals such as "bugs", "boxes" and "classes" match their singular forms. Meaningful words ("US", "memory") are no longer discarded, and Chinese and Japanese phrases match inside longer text. Benchmark results were unchanged, query by query.
- **Store and import.** One import stores its evidence once rather than once per line. Only Markdown headings are skipped (`#1 priority` is kept), numbered list markers are removed, and Unicode line separators no longer split a line. An explicit empty start date is rejected instead of becoming "now". `REPLACE` statements can no longer rewrite evidence or audit events. Lock ordering is consistent, so threads sharing one store do not stall. The Markdown projection survives an unwritable path and a topic capitalization change, and skips unchanged files (a single add with 3,000 memories went from 1.08 s to 0.30 s).
- **Server, CLI and dashboard.** `HEAD` requests and the favicon work. Clear 400 errors for non-date `as_of` values, deeply nested JSON, negative lengths and missing content. Oversized uploads receive their 413. Missing IDs read "Not found", and the CLI reports database errors without a traceback. In the dashboard, choosing today's date includes today's additions, blank scores keep their defaults, and proposal cards count only records that actually change.

To reproduce:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
node --check src/memory_agent/static/app.js
python3 run.py benchmark
python3 run.py serve --demo
```

The bundled 22-query synthetic regression produced:

| Strategy | Recall@3 | Top-1 retrieval accuracy | Unknown-query abstention | Invalid-time hits |
| --- | ---: | ---: | ---: | ---: |
| Temporal hybrid | 88.89% | 88.89% | 100% | 0% |
| Temporal lexical | 88.89% | 88.89% | 100% | 0% |
| Temporal feature hash | 88.89% | 88.89% | 100% | 0% |
| Temporal recent context | 50.00% | 38.89% | 0% | 0% |
| Time-blind keyword | 88.89% | 61.11% | 50% | 48.57% |

Recall and top-1 accuracy use 18 answerable queries; abstention uses four unanswerable queries. Invalid-time hits are the fraction of returned records outside their requested validity interval. The two synonym-only queries expose the lack of semantic embeddings. The consolidation check reduced two active duplicates to one, retained both records and both sources, and restored both active records on undo.

These measurements describe the included handcrafted workload only. The benchmark does not establish superiority over production RAG or evaluate generated answers. Re-run to measure latency on your own hardware.
