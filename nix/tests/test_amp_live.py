import importlib.util
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("amp_live", sys.argv.pop(1))
sys.path.insert(0, str(Path(spec.origin).parent))
amp_live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amp_live)

THREAD = "T-00000000-1111-2222-3333-444444444444"


class Handler(BaseHTTPRequestHandler):
    requests = []
    descriptor = None
    post_status = 204

    def _auth(self):
        return self.headers.get("Authorization") == "Bearer secret"

    def do_GET(self):
        type(self).requests.append(("GET", self._auth(), None))
        body = json.dumps(type(self).descriptor).encode()
        self.send_response(200 if self._auth() else 401)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).requests.append(("POST", self._auth(), json.loads(body)))
        self.send_response(type(self).post_status if self._auth() else 401)
        self.end_headers()

    def log_message(self, *_):
        pass


class AmpLiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / "home"
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir(mode=0o700)
        session = self.home / ".cache/rediff/amp/session-one"
        session.mkdir(parents=True, mode=0o700)
        os.chmod(session.parent, 0o700)
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)
        self.connection = session / "connection.json"
        self.descriptor = {"version": 1, "url": f"http://127.0.0.1:{self.server.server_port}/feedback",
                           "token": "secret", "root": str(self.root.resolve()), "thread": THREAD, "title": "Review"}
        Handler.descriptor = dict(self.descriptor)
        Handler.requests = []
        Handler.post_status = 204
        self.write_descriptor()
        self.payload = Path(self.temp.name) / "submission.json"

    def write_descriptor(self):
        self.connection.write_text(json.dumps(self.descriptor))
        os.chmod(self.connection, 0o600)

    def discover(self):
        with patch.dict(os.environ, {"HOME": str(self.home)}):
            return amp_live.discover(self.root)

    def test_reload_requests_are_small_scoped_and_not_claimed_complete(self):
        for wrong_thread, wrong_root in (("T-other", self.root), (THREAD, self.home)):
            with self.assertRaisesRegex(ValueError, "nothing sent"):
                amp_live.request_reload(self.connection, wrong_thread, wrong_root)
        self.assertEqual([], Handler.requests)
        Handler.descriptor["thread"] = "T-other"
        with self.assertRaisesRegex(RuntimeError, "nothing sent"):
            amp_live.request_reload(self.connection, THREAD, self.root)
        self.assertFalse(any(method == "POST" for method, *_ in Handler.requests))
        Handler.descriptor = dict(self.descriptor)
        result = amp_live.request_reload(self.connection, THREAD, self.root)
        self.assertIn("queued", result)
        self.assertIn("not confirmation", result)
        method, authenticated, body = Handler.requests[-1]
        self.assertEqual(("POST", True), (method, authenticated))
        self.assertIn("reload_plugins", body["content"])
        self.assertLess(len(json.dumps(body)), 600)
        amp_live.request_reload(self.connection, THREAD, self.root)
        self.assertNotEqual(body["id"], Handler.requests[-1][2]["id"])
        Handler.post_status = 500
        before = len(Handler.requests)
        with self.assertRaisesRegex(RuntimeError, "uncertain"):
            amp_live.request_reload(self.connection, THREAD, self.root)
        self.assertEqual(2, len(Handler.requests) - before, "No automatic retry after an uncertain POST")

    def payload_write(self, **extra):
        value = {"submission_id": "one", "repository": str(self.root.resolve()),
                 "comments": [{"file": "demo.lua", "side": "new", "line": 7,
                               "text": "fix this", "snapshot_id": "s"}],
                 "snapshots": {"s": {"text": "old"}}}
        value.update(extra)
        self.payload.write_text(json.dumps(value))

    def test_discovery_is_authenticated_get_only_and_returns_no_secret(self):
        found = self.discover()
        self.assertEqual([{"connection": str(self.connection.absolute()), "session": THREAD, "title": "Review"}], found)
        self.assertEqual([("GET", True, None)], Handler.requests)
        self.assertNotIn("secret", json.dumps(found))

    def test_discovery_does_not_fall_back_to_old_registry(self):
        old = self.home / ".cache/anthrodiff/amp/session-one"
        old.mkdir(parents=True, mode=0o700)
        os.chmod(old.parent, 0o700)
        old_connection = old / "connection.json"
        old_connection.write_text(json.dumps(self.descriptor))
        os.chmod(old_connection, 0o600)
        self.connection.unlink()
        self.assertEqual([], self.discover())
        self.assertEqual([], Handler.requests)

    def test_discovery_ignores_root_mismatch_dead_and_unsafe_entries(self):
        self.descriptor["root"] = str(Path(self.temp.name) / "other")
        self.write_descriptor()
        self.assertEqual([], self.discover())
        self.assertEqual([], Handler.requests)
        self.descriptor["root"] = str(self.root.resolve())
        self.write_descriptor()
        os.chmod(self.connection, 0o644)
        self.assertEqual([], self.discover())
        os.chmod(self.connection, 0o600)
        self.descriptor["url"] = "http://127.0.0.1:1/feedback"
        self.write_descriptor()
        self.assertEqual([], self.discover())

    def test_review_and_general_message_payloads_and_receipt_dedupe(self):
        self.payload_write()
        ack = amp_live.send(self.connection, THREAD, self.payload)
        self.assertEqual("accepted", ack["status"])
        sent = Handler.requests[-1][2]
        self.assertEqual("one", sent["id"])
        self.assertIn("fix this", sent["content"])
        self.assertIn("user owns staging", sent["content"])
        self.assertEqual(ack, amp_live.send(self.connection, THREAD, self.payload))
        self.assertEqual(1, sum(request[0] == "POST" for request in Handler.requests))
        self.payload = Path(self.temp.name) / "message.json"
        self.payload_write(message="Please explain this", submission_id="two")
        amp_live.send(self.connection, THREAD, self.payload)
        message = json.loads(Handler.requests[-1][2]["content"])
        self.assertEqual(["rules", "repository", "message"], list(message))
        self.assertEqual("Please explain this", message["message"])
        self.assertNotIn("fix this", Handler.requests[-1][2]["content"])

    def test_only_204_accepts_and_retry_reuses_id(self):
        self.payload_write()
        Handler.post_status = 500
        with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
            amp_live.send(self.connection, THREAD, self.payload)
        first_id = Handler.requests[-1][2]["id"]
        Handler.post_status = 204
        amp_live.send(self.connection, THREAD, self.payload)
        self.assertEqual(first_id, Handler.requests[-1][2]["id"])

    def test_review_sends_references_not_full_files_and_keeps_archive(self):
        note = {"file": "src/space name.lua", "side": "old", "line": 17, "line_end": 19,
                "text": "Keep the boundary check", "snapshot_id": "s",
                "selection": {"kind": "block", "coordinates": "nvim-getregionpos-v1", "tabstop": 4,
                              "spans": [
                                  {"line": 17, "start_byte": 1, "start_offset": 2, "end_byte": 6, "end_offset": 0},
                                  {"line": 18, "start_byte": 3, "start_offset": 0, "end_byte": 8, "end_offset": 0},
                                  {"line": 19, "start_byte": 2, "start_offset": 1, "end_byte": 2, "end_offset": 3},
                              ], "text": ["selected α" * 200]}}
        self.payload_write(comments=[note], snapshot_status={"s": "changed"},
                           snapshots={"s": {"group": "staged", "old": "OLD FILE " * 150000,
                                             "new": "NEW FILE " * 150000, "patch": "FULL PATCH"}})
        original = self.payload.read_bytes()
        amp_live.send(self.connection, THREAD, self.payload)
        content = Handler.requests[-1][2]["content"]
        self.assertLess(len(content.encode()), 3072)
        message = json.loads(content)
        self.assertEqual(["rules", "repository", "snapshot_archive", "annotations"], list(message))
        self.assertEqual(str(self.payload.absolute()), message["snapshot_archive"])
        self.assertEqual([{
            **{key: note[key] for key in ("side", "line", "line_end", "snapshot_id", "text")},
            "file": str(self.root.resolve() / note["file"]), "comparison": "staged", "snapshot_status": "changed",
            "selection": {key: value for key, value in note["selection"].items() if key != "text"},
            "selected_text": "selected α" * 40, "selected_text_truncated": True,
        }], message["annotations"])
        for excluded in ("OLD FILE", "NEW FILE", "FULL PATCH", "selected α" * 41, '"snapshots"', '"comments"'):
            self.assertNotIn(excluded, content)
        self.assertEqual(original, self.payload.read_bytes())

    def test_changed_archived_snapshot_cannot_reuse_uncertain_receipt(self):
        self.payload_write()
        Handler.post_status = 500
        with self.assertRaises(RuntimeError):
            amp_live.send(self.connection, THREAD, self.payload)
        self.payload_write(snapshots={"s": {"text": "different history"}})
        with self.assertRaisesRegex(RuntimeError, "content differs"):
            amp_live.send(self.connection, THREAD, self.payload)
        self.assertEqual(1, sum(request[0] == "POST" for request in Handler.requests))

    def test_generation_payload_target_and_size_changes_are_refused(self):
        self.payload_write()
        Handler.post_status = 500
        with self.assertRaises(RuntimeError):
            amp_live.send(self.connection, THREAD, self.payload)
        self.descriptor["title"] = "new generation"
        self.write_descriptor()
        with self.assertRaisesRegex(RuntimeError, "identity"):
            amp_live.send(self.connection, THREAD, self.payload)
        self.connection.unlink()
        self.descriptor["title"] = "Review"
        self.write_descriptor()
        self.payload = Path(self.temp.name) / "wrong.json"
        self.payload_write(repository="/wrong")
        with self.assertRaisesRegex(ValueError, "repository"):
            amp_live.send(self.connection, THREAD, self.payload)
        self.payload = Path(self.temp.name) / "large.json"
        self.payload_write(message="x" * amp_live.MAX_BODY)
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            amp_live.send(self.connection, THREAD, self.payload)

    def test_mismatched_live_identity_prevents_post(self):
        self.payload_write()
        Handler.descriptor["thread"] = "T-other"
        self.assertEqual([], self.discover())
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            amp_live.send(self.connection, THREAD, self.payload)
        self.assertTrue(all(request[0] == "GET" for request in Handler.requests))

    def test_changed_content_cannot_reuse_uncertain_receipt(self):
        self.payload_write(message="first")
        Handler.post_status = 500
        with self.assertRaises(RuntimeError):
            amp_live.send(self.connection, THREAD, self.payload)
        self.payload_write(message="second")
        with self.assertRaisesRegex(RuntimeError, "content differs"):
            amp_live.send(self.connection, THREAD, self.payload)
        self.assertEqual(1, sum(request[0] == "POST" for request in Handler.requests))

    def test_invalid_token_is_not_echoed_and_remote_endpoints_are_refused(self):
        self.payload_write()
        self.descriptor["token"] = "secret\ninvalid"
        self.write_descriptor()
        with self.assertRaises(ValueError) as caught:
            amp_live.send(self.connection, THREAD, self.payload)
        self.assertNotIn("secret", str(caught.exception))
        self.descriptor["token"] = "secret"
        self.descriptor["url"] = "http://example.com:80/feedback"
        self.write_descriptor()
        with self.assertRaisesRegex(ValueError, "descriptor"):
            amp_live.send(self.connection, THREAD, self.payload)
        self.assertEqual([], Handler.requests)

    def test_failed_receipt_replacement_retains_uncertain_retry_state(self):
        self.payload_write()
        with patch.object(amp_live.os, "replace", side_effect=OSError("disk error")):
            with self.assertRaisesRegex(OSError, "disk error"):
                amp_live.send(self.connection, THREAD, self.payload)
        receipt = Path(str(self.payload) + ".amp-live-receipt.json")
        self.assertEqual("uncertain", json.loads(receipt.read_text())["status"])
        self.assertEqual(0o600, receipt.stat().st_mode & 0o777)
        self.assertEqual("accepted", amp_live.send(self.connection, THREAD, self.payload)["status"])
        posts = [request[2] for request in Handler.requests if request[0] == "POST"]
        self.assertEqual(posts[0], posts[1])


unittest.main()
