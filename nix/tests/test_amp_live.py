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
        session = self.home / ".cache/anthrodiff/amp/session-one"
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

    def payload_write(self, **extra):
        value = {"submission_id": "one", "repository": str(self.root.resolve()),
                 "comments": [{"text": "fix this"}], "snapshots": {"s": {"text": "old"}}}
        value.update(extra)
        self.payload.write_text(json.dumps(value))

    def test_discovery_is_authenticated_get_only_and_returns_no_secret(self):
        found = self.discover()
        self.assertEqual([{"connection": str(self.connection.absolute()), "session": THREAD, "title": "Review"}], found)
        self.assertEqual([("GET", True, None)], Handler.requests)
        self.assertNotIn("secret", json.dumps(found))

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
        self.assertTrue(Handler.requests[-1][2]["content"].startswith("Please explain this"))
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
