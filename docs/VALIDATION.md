# Validation record

Validated on 23 October 2026 on macOS with Python 3.9.6 and Python 3.11 (tests), Python 3.11 (wheel build), Node 26 (JavaScript syntax), and Chrome (dashboard).

- 68 automated tests passed on both Python versions, including the real-socket HTTP tests and the same scenarios through an in-memory transport.
- An installable wheel built without runtime dependencies and includes every dashboard asset.
- Dashboard JavaScript passed `node --check`.
- The dashboard was exercised in a real browser against a fresh demo collection: applying the changing-status proposal, dated recall (`Project Atlas status` as of 2026-01-15 returns the prototype value and excludes beta), relevance feedback, and the evaluation lab all worked. The layout was checked at desktop and 375 px phone width without horizontal scrolling. The screenshots in `docs/images/` come from that session.
- That pass found two small server gaps, both now fixed and tested: `HEAD` requests returned 501, and the browser's favicon request returned 404.

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
