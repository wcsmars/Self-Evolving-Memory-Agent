"""Local JSON API and dashboard using the Python standard library."""
import json
import mimetypes
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .benchmark import run_benchmark
from .demo import seed_demo
from .evolution import EvolutionEngine
from .ingest import ingest
from .retrieval import Retriever
from .store import Store, VersionConflict

MAX_BODY = 1_048_576


class App:
    def __init__(self, root):
        self.store = Store(root)
        self.evolution = EvolutionEngine(self.store)
        self.retriever = Retriever(self.store)
        self.write_lock = threading.RLock()

    def overview(self):
        return {"stats": self.store.get_stats(),
                "learning": self.retriever.learning_state(),
                "proposals": self.evolution.list_proposals(),
                "events": self.store.list_events(limit=20)}

    def dispatch(self, method, path, params, body):
        if method == "GET":
            if path == "/api/overview":
                return self.overview()
            if path == "/api/memories":
                return {"memories": self.store.list_memories(
                    include_inactive=params.get("include_inactive", ["false"])[0] == "true",
                    as_of=params.get("as_of", [None])[0],
                    topic=params.get("topic", [None])[0], kind=params.get("kind", [None])[0])}
            if path == "/api/proposals":
                return {"proposals": self.evolution.list_proposals(status=params.get("status", [None])[0])}
            if path == "/api/events":
                return {"events": self.store.list_events(limit=100)}
            if path.startswith("/api/sources/"):
                return self.store.get_source(path.rsplit("/", 1)[1])
            if path == "/api/health":
                return {"status": "ok", "version": "0.1.0"}
        if method == "POST":
            if path == "/api/benchmark":
                return run_benchmark()
            with self.write_lock:
                if path == "/api/memories":
                    allowed = {"content", "kind", "topic", "subject", "predicate", "value", "valid_from", "valid_until", "confidence", "importance", "source", "source_text"}
                    unknown = set(body) - allowed
                    if unknown:
                        raise ValueError("Unknown fields: " + ", ".join(sorted(unknown)))
                    return {"memory": self.store.add_memory(**body)}
                if path == "/api/ingest":
                    return {"memories": ingest(self.store, body.get("text"), source=body.get("source", "text import"), topic=body.get("topic", "inbox"))}
                if path == "/api/search":
                    return self.retriever.search(body.get("query", ""), as_of=body.get("as_of") or None, limit=body.get("limit", 8))
                if path == "/api/feedback":
                    return {"learning": self.retriever.feedback(body.get("retrieval_id"), body.get("relevant_ids", []))}
                if path == "/api/review":
                    return {"proposals": self.evolution.review()}
                if path == "/api/demo":
                    result = seed_demo(self.store)
                    self.evolution.review()
                    return {**self.overview(), **result}
                parts = path.strip("/").split("/")
                if len(parts) == 4 and parts[:2] == ["api", "memories"] and parts[3] == "update":
                    version = body.get("expected_version")
                    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                        raise ValueError("expected_version must be a positive integer; reload the memory before editing")
                    return {"memory": self.store.update_memory(parts[2], body.get("changes", {}), expected_version=body["expected_version"])}
                if len(parts) == 4 and parts[:2] == ["api", "proposals"]:
                    actions = {"apply": self.evolution.apply, "reject": self.evolution.reject, "undo": self.evolution.undo}
                    if parts[3] in actions:
                        return actions[parts[3]](parts[2])
        raise KeyError("Endpoint not found")


def make_server(root, host="127.0.0.1", port=8765):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("This unauthenticated development server only binds to loopback")
    app = App(root)
    static = Path(__file__).parent / "static"

    class Handler(BaseHTTPRequestHandler):
        server_version = "MemoryAgent/0.1"

        def _same_origin(self):
            try:
                hostname = urlparse("//" + self.headers.get("Host", "")).hostname
                if hostname not in {"localhost", "127.0.0.1", "::1"}:
                    return False
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers.get("Host", ""):
                    return False
                return self.headers.get("Sec-Fetch-Site") != "cross-site"
            except ValueError:
                return False

        def _send(self, status, body, content_type="application/json; charset=utf-8"):
            data = json.dumps(body, ensure_ascii=False).encode() if not isinstance(body, bytes) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _handle(self, method):
            if not self._same_origin():
                self._send(403, {"error": "Only same-origin localhost requests are accepted"})
                return
            parsed = urlparse(self.path)
            if method == "GET" and not parsed.path.startswith("/api/"):
                names = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                         "/static/app.js": "app.js", "/static/style.css": "style.css",
                         "/favicon.ico": "favicon.svg", "/static/favicon.svg": "favicon.svg"}
                if parsed.path not in names:
                    self._send(404, {"error": "Not found"})
                    return
                target = static / names[parsed.path]
                if not target.is_file():
                    self._send(404, {"error": "Dashboard asset missing"})
                    return
                self._send(200, target.read_bytes(), (mimetypes.guess_type(target.name)[0] or "text/plain") + "; charset=utf-8")
                return
            try:
                body = {}
                if method == "POST":
                    if self.headers.get("Transfer-Encoding"):
                        raise ValueError("Chunked requests are not supported")
                    length = int(self.headers.get("Content-Length", "0"))
                    if length < 0 or length > MAX_BODY:
                        self._send(413, {"error": "Request body is limited to 1 MiB"})
                        return
                    if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                        self._send(415, {"error": "Use Content-Type: application/json"})
                        return
                    body = json.loads(self.rfile.read(length) or b"{}")
                    if not isinstance(body, dict):
                        raise ValueError("Request body must be a JSON object")
                result = app.dispatch(method, parsed.path, parse_qs(parsed.query), body)
                self._send(200, result)
            except KeyError as exc:
                self._send(404, {"error": str(exc).strip("'")})
            except VersionConflict as exc:
                self._send(409, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self._send(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._send(409, {"error": str(exc)})
            except Exception:
                import traceback
                traceback.print_exc()
                self._send(500, {"error": "Unexpected server error; see the local terminal"})

        def do_GET(self):
            self._handle("GET")

        def do_HEAD(self):
            # Same headers as GET without a body; GET handlers are read-only.
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    if host == "::1":
        class LocalIPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        server = LocalIPv6Server((host, port), Handler)
    else:
        server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
    return server


def serve(root, host="127.0.0.1", port=8765, demo=False, review_interval=300):
    if review_interval < 0:
        raise ValueError("review_interval must be nonnegative")
    server = make_server(root, host, port)
    if demo:
        seed_demo(server.app.store)
        server.app.evolution.review()
    stop = threading.Event()

    def maintenance():
        while not stop.wait(review_interval):
            try:
                with server.app.write_lock:
                    server.app.evolution.review()
            except Exception as exc:
                print("Memory review failed: {}".format(exc), flush=True)

    if review_interval:
        threading.Thread(target=maintenance, daemon=True).start()
    display_host = "[::1]" if host == "::1" else host
    print("Memory agent: http://{}:{}".format(display_host, server.server_port), flush=True)
    print("Data: {} | Reviews propose changes; apply them in the dashboard.".format(Path(root).resolve()), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()

