import argparse
import json
import sys
import time
from pathlib import Path

from .demo import seed_demo
from .evolution import EvolutionEngine
from .ingest import ingest
from .retrieval import Retriever
from .store import Store


def parser():
    p = argparse.ArgumentParser(description="Evidence-preserving, self-evolving local memory")
    p.add_argument("--root", default=".memory-agent", help="Private data directory (default: .memory-agent)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create an empty memory store")
    sub.add_parser("demo", help="Seed synthetic examples into an empty store")
    add = sub.add_parser("add", help="Add one memory with its evidence")
    add.add_argument("content")
    add.add_argument("--kind", default="note", choices=["fact", "note", "event", "preference", "procedure"])
    add.add_argument("--topic", default="inbox")
    for name in ("subject", "predicate", "value", "valid-from", "valid-until"):
        add.add_argument("--" + name)
    add.add_argument("--source", default="CLI")
    imp = sub.add_parser("ingest", help="Import UTF-8 text; one memory per line")
    imp.add_argument("file", type=Path)
    imp.add_argument("--topic", default="inbox")
    update = sub.add_parser("update", help="Manually correct a memory, retaining evidence and revisions")
    update.add_argument("memory_id")
    update.add_argument("--version", type=int, required=True, help="Expected current version")
    update.add_argument("--changes", required=True, help='JSON changes, e.g. {"valid_until":"2026-02-01"}')
    search = sub.add_parser("search", help="Retrieve current or historical memories")
    search.add_argument("query")
    search.add_argument("--as-of")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--strategy", choices=["hybrid", "lexical", "vector", "full_context"], default="hybrid")
    ls = sub.add_parser("list", help="List memories")
    ls.add_argument("--all", action="store_true")
    ls.add_argument("--as-of")
    sub.add_parser("review", help="Propose consolidation, classification and historical corrections")
    sub.add_parser("proposals", help="List proposed and completed changes")
    for name in ("apply", "reject", "undo"):
        action = sub.add_parser(name, help=name.capitalize() + " an evolution proposal")
        action.add_argument("proposal_id")
    feedback = sub.add_parser("feedback", help="Mark a retrieval's relevant results")
    feedback.add_argument("retrieval_id")
    feedback.add_argument("relevant_ids", nargs="*")
    sub.add_parser("stats", help="Show memory and learning statistics")
    sub.add_parser("export", help="Refresh the Markdown projection")
    bench = sub.add_parser("benchmark", help="Run the isolated synthetic retrieval benchmark")
    bench.add_argument("--svg", type=Path, help="Also write a strategy comparison chart to this SVG file")
    watch = sub.add_parser("watch", help="Periodically propose changes without applying them")
    watch.add_argument("--interval", type=float, default=300)
    watch.add_argument("--once", action="store_true")
    web = sub.add_parser("serve", help="Launch the local dashboard and API")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--demo", action="store_true")
    web.add_argument("--review-interval", type=float, default=300)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            from .server import serve
            serve(args.root, host=args.host, port=args.port, demo=args.demo, review_interval=args.review_interval)
            return 0
        if args.command == "benchmark":
            from .benchmark import render_svg, run_benchmark
            result = run_benchmark()
            if args.svg is not None:
                args.svg.parent.mkdir(parents=True, exist_ok=True)
                args.svg.write_text(render_svg(result), encoding="utf-8")
        else:
            store = Store(args.root)
            engine = EvolutionEngine(store)
            retriever = Retriever(store)
            cmd = args.command
            if cmd == "init":
                result = {"root": str(Path(args.root).resolve()), "stats": store.get_stats()}
            elif cmd == "demo":
                result = {**seed_demo(store), "proposals": engine.review()}
            elif cmd == "add":
                keys = ("content", "kind", "topic", "subject", "predicate", "value", "valid_from", "valid_until", "source")
                result = store.add_memory(**{key: getattr(args, key) for key in keys})
            elif cmd == "ingest":
                result = {"memories": ingest(store, args.file.read_text(encoding="utf-8"), source=args.file.name, topic=args.topic)}
            elif cmd == "search":
                result = retriever.search(args.query, as_of=args.as_of, limit=args.limit, strategy=args.strategy)
            elif cmd == "update":
                result = store.update_memory(args.memory_id, json.loads(args.changes), expected_version=args.version)
            elif cmd == "list":
                result = store.list_memories(include_inactive=args.all, as_of=args.as_of)
            elif cmd == "review":
                result = engine.review()
            elif cmd == "proposals":
                result = engine.list_proposals()
            elif cmd in {"apply", "reject", "undo"}:
                result = getattr(engine, cmd)(args.proposal_id)
            elif cmd == "feedback":
                result = retriever.feedback(args.retrieval_id, args.relevant_ids)
            elif cmd == "stats":
                result = {"stats": store.get_stats(), "learning": retriever.learning_state()}
            elif cmd == "export":
                store.export_markdown()
                result = {"path": str((Path(args.root) / "memory").resolve())}
            elif cmd == "watch":
                if args.interval < 1:
                    raise ValueError("interval must be at least one second")
                while True:
                    print(json.dumps({"pending": engine.review()}, ensure_ascii=False), flush=True)
                    if args.once:
                        return 0
                    time.sleep(args.interval)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except KeyboardInterrupt:
        return 0
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        print("Error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
