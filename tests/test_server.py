"""Exercise the real loopback HTTP surface and persistent workflow together."""
from contextlib import redirect_stderr
import http.client
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch

from memory_agent.server import App, MAX_BODY, make_server


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        try:
            self.server = make_server(self.temp.name, port=0)
        except PermissionError:
            self.temp.cleanup()
            self.skipTest("This environment forbids loopback socket binds; HTTP transport tests run separately")
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        supplied = dict(headers or {})
        data = raw
        if method == "POST":
            supplied.setdefault("Content-Type", "application/json")
            if data is None:
                data = json.dumps(body if body is not None else {}).encode("utf-8")
        try:
            with redirect_stderr(io.StringIO()):
                connection.request(method, path, body=data, headers=supplied)
                response = connection.getresponse()
                content = response.read()
            return response.status, json.loads(content)
        finally:
            connection.close()

    def success(self, method, path, body=None):
        status, result = self.request(method, path, body)
        self.assertEqual(status, 200, result)
        return result

    def add(self, text, **kwargs):
        fields = {"content": text, "valid_from": "2020-01-01", **kwargs}
        return self.success("POST", "/api/memories", fields)["memory"]

    def test_demo_review_apply_undo_retains_sources_and_persists_feedback(self):
        seeded = self.success("POST", "/api/demo")
        self.assertTrue(seeded["seeded"])
        before = self.success("GET", "/api/memories")["memories"]
        proposals = self.success("POST", "/api/review")["proposals"]
        duplicate = next(item for item in proposals if item["kind"] == "deduplicate")
        evidence = {
            source_id: self.success("GET", "/api/sources/" + source_id)
            for source_id in duplicate["evidence_ids"]
        }
        applied = self.success("POST", "/api/proposals/" + duplicate["id"] + "/apply")
        self.assertEqual(applied["status"], "applied")
        after = self.success("GET", "/api/memories")["memories"]
        self.assertEqual(len(after), len(before) - 1)
        for source_id, original in evidence.items():
            self.assertEqual(self.success("GET", "/api/sources/" + source_id), original)
        undone = self.success("POST", "/api/proposals/" + duplicate["id"] + "/undo")
        self.assertEqual(undone["status"], "reverted")
        self.assertEqual(len(self.success("GET", "/api/memories")["memories"]), len(before))

        search = self.success("POST", "/api/search", {"query": "Atlas SQLite"})
        relevant_id = search["results"][0]["memory"]["id"]
        feedback = self.success("POST", "/api/feedback", {
            "retrieval_id": search["retrieval_id"], "relevant_ids": [relevant_id]
        })
        self.assertEqual(feedback["learning"]["feedback_count"], 1)
        restarted = App(self.temp.name)
        self.assertEqual(restarted.retriever.learning_state()["feedback_count"], 1)
        self.assertEqual(restarted.evolution.get_proposal(duplicate["id"])["status"], "reverted")
        self.assertTrue(self.success("GET", "/api/events")["events"])
        self.assertFalse(self.success("POST", "/api/demo")["seeded"])

    def test_reviewed_chronology_preserves_historical_fact_at_boundary(self):
        old = self.add("Atlas release status: pending", kind="fact", subject="Atlas", predicate="release status", value="pending")
        new = self.add("Atlas release status: beta", kind="fact", subject="Atlas", predicate="release status", value="beta", valid_from="2021-01-01")
        proposal = next(item for item in self.success("POST", "/api/review")["proposals"] if item["kind"] == "supersede")
        self.success("POST", "/api/proposals/" + proposal["id"] + "/apply")
        historical = self.success("POST", "/api/search", {"query": "Atlas release status", "as_of": "2020-06-01"})
        self.assertEqual([item["memory"]["id"] for item in historical["results"]], [old["id"]])
        boundary = self.success("POST", "/api/search", {"query": "Atlas release status", "as_of": "2021-01-01"})
        self.assertEqual([item["memory"]["id"] for item in boundary["results"]], [new["id"]])
        source = self.success("GET", "/api/sources/" + old["source_id"])
        self.assertEqual(source["text"], old["content"])

    def test_manual_update_requires_correct_version_and_preserves_evidence(self):
        memory = self.add("Atlas status: draft", subject="Atlas", predicate="status", value="draft")
        endpoint = "/api/memories/" + memory["id"] + "/update"
        for version_fields in ({}, {"expected_version": None}, {"expected_version": True}):
            with self.subTest(version_fields=version_fields):
                status, result = self.request("POST", endpoint, {"changes": {"value": "unchecked"}, **version_fields})
                self.assertEqual(status, 400, result)
        updated = self.success("POST", endpoint, {
            "expected_version": memory["version"],
            "changes": {"content": "Atlas status: reviewed", "value": "reviewed"},
        })["memory"]
        self.assertEqual(updated["version"], memory["version"] + 1)
        status, result = self.request("POST", endpoint, {
            "expected_version": memory["version"], "changes": {"value": "stale overwrite"}
        })
        self.assertIn(status, (400, 409), result)
        current = self.server.app.store.get_memory(memory["id"])
        self.assertEqual(current["value"], "reviewed")
        self.assertEqual(self.success("GET", "/api/sources/" + memory["source_id"])["text"], memory["content"])

    def test_import_endpoint_returns_verbatim_notes_and_dates(self):
        text = "# Meeting notes\n[2020-02-03] Atlas | owner | Taylor\nA tentative idea; do not infer approval."
        imported = self.success("POST", "/api/ingest", {"text": text, "source": "Synthetic notes", "topic": "projects/atlas"})
        self.assertEqual(len(imported["memories"]), 2)
        fact, note = imported["memories"]
        self.assertTrue(fact["valid_from"].startswith("2020-02-03T"))
        self.assertEqual(fact["content"], "[2020-02-03] Atlas | owner | Taylor")
        self.assertEqual(note["kind"], "note")
        self.assertIsNone(note["subject"])
        self.assertEqual(self.success("GET", "/api/sources/" + note["source_id"])["text"], text)

    def test_bad_payloads_are_rejected_without_mutating_collection(self):
        cases = [
            ({"raw": b"{broken"}, 400),
            ({"raw": b"[]"}, 400),
            ({"body": {"content": "bad type"}, "headers": {"Content-Type": "text/plain"}}, 415),
            ({"body": {"content": "bad type"}, "headers": {"Content-Type": "application/json-invalid"}}, 415),
            ({"raw": b"{}", "headers": {"Content-Length": str(MAX_BODY + 1)}}, 413),
            ({"body": {"content": "bad topic", "topic": "../../escape"}}, 400),
            ({"body": {"content": "unknown field", "accidental": True}}, 400),
            ({"body": {"topic": "missing content"}}, 400),
            ({"raw": b"[" * 100_000 + b"]" * 100_000}, 400),
            ({"raw": b"{}", "headers": {"Content-Length": "-1"}}, 400),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                status, result = self.request("POST", "/api/memories", **args)
                self.assertEqual(status, expected, result)
                self.assertIn("error", result)
        self.assertEqual(self.success("GET", "/api/memories?include_inactive=true")["memories"], [])

    def test_origin_host_and_file_paths_are_guarded(self):
        for headers in ({"Origin": "https://example.test"}, {"Host": "evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                status, _ = self.request("POST", "/api/memories", {"content": "should not be saved"}, headers=headers)
                self.assertEqual(status, 403)
        for path in ("/../../index.sqlite3", "/evidence/source.json", "/api/sources/missing", "/api/unknown"):
            with self.subTest(path=path):
                status, result = self.request("GET", path)
                self.assertEqual(status, 404, result)
        self.assertEqual(self.success("GET", "/api/memories")["memories"], [])
        self.assertEqual(self.success("GET", "/api/health")["status"], "ok")
        with self.assertRaises(ValueError):
            make_server(self.temp.name, host="0.0.0.0", port=0)

    def test_search_rejects_non_date_as_of_values(self):
        for value in (False, 0, [], {}, 123):
            with self.subTest(value=value):
                status, result = self.request("POST", "/api/search", {"query": "x", "as_of": value})
                self.assertEqual(status, 400, result)
        self.assertEqual(self.success("POST", "/api/search", {"query": "x", "as_of": ""})["as_of"], None)

    def raw(self, method, path):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            with redirect_stderr(io.StringIO()):
                connection.request(method, path)
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_head_matches_get_headers_without_body_and_favicon_is_served(self):
        for path in ("/", "/static/app.js", "/api/health"):
            with self.subTest(path=path):
                get_status, get_headers, get_body = self.raw("GET", path)
                head_status, head_headers, head_body = self.raw("HEAD", path)
                self.assertEqual((get_status, head_status), (200, 200))
                self.assertEqual(head_body, b"")
                self.assertEqual(head_headers["Content-Length"], str(len(get_body)))
                self.assertEqual(head_headers["Content-Type"], get_headers["Content-Type"])
        for path in ("/favicon.ico", "/static/favicon.svg"):
            with self.subTest(path=path):
                status, headers, body = self.raw("GET", path)
                self.assertEqual(status, 200)
                self.assertTrue(headers["Content-Type"].startswith("image/svg+xml"))
                self.assertTrue(body.startswith(b"<svg"))


class OversizedUploadTests(unittest.TestCase):
    """A client that is still uploading must receive the 413, not a reset."""

    def test_oversized_body_receives_413(self):
        with tempfile.TemporaryDirectory() as root:
            try:
                server = make_server(root, port=0)
            except PermissionError:
                self.skipTest("This environment forbids loopback socket binds")
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
                with redirect_stderr(io.StringIO()):
                    connection.request("POST", "/api/memories", body=b"x" * (2 * MAX_BODY),
                                       headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    self.assertEqual(response.status, 413)
                    self.assertIn("1 MiB", json.loads(response.read())["error"])
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class _MemorySocket:
    """Only the byte transport is replaced; request parsing and App remain real."""

    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = io.BytesIO()

    def makefile(self, *args, **kwargs):
        return self.input

    def sendall(self, data):
        self.output.write(data)


class _TransportServer:
    def __init__(self, address, handler):
        self.server_address = address
        self.server_port = 8765
        self.handler = handler


class MemoryHTTPTransportTests(ServerIntegrationTests):
    """The same HTTP scenarios without opening sockets, useful in sandboxes."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch("memory_agent.server.ThreadingHTTPServer", _TransportServer):
            self.server = make_server(self.temp.name, port=8765)

    def tearDown(self):
        self.temp.cleanup()

    def raw(self, method, path):
        request = "{} {} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: close\r\n\r\n".format(method, path)
        transport = _MemorySocket(request.encode("ascii"))
        with redirect_stderr(io.StringIO()):
            self.server.handler(transport, ("127.0.0.1", 12345), self.server)
        head, _, body = transport.output.getvalue().partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        headers = dict(line.split(": ", 1) for line in lines[1:])
        return int(lines[0].split(" ")[1]), headers, body

    def request(self, method, path, body=None, headers=None, raw=None):
        supplied = {"Host": "127.0.0.1:8765", "Connection": "close", **(headers or {})}
        data = raw or b""
        if method == "POST":
            supplied.setdefault("Content-Type", "application/json")
            if raw is None:
                data = json.dumps(body if body is not None else {}).encode("utf-8")
            supplied.setdefault("Content-Length", str(len(data)))
        head = "\r\n".join([method + " " + path + " HTTP/1.1"] + [key + ": " + value for key, value in supplied.items()])
        transport = _MemorySocket(head.encode("ascii") + b"\r\n\r\n" + data)
        with redirect_stderr(io.StringIO()):
            self.server.handler(transport, ("127.0.0.1", 12345), self.server)
        response_headers, _, content = transport.output.getvalue().partition(b"\r\n\r\n")
        status = int(response_headers.split(b" ")[1])
        return status, json.loads(content)


if __name__ == "__main__":
    unittest.main()
