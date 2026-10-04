"""Safe stdlib bridge from myeditor submissions to the anthrodiff Amp plugin."""

import hashlib
import http.client
import json
import os
import stat
import sys
import tempfile
import urllib.parse
import uuid
from pathlib import Path

from harness import GUIDANCE, review_prompt

MAX_BODY = 1024 * 1024


def _private(path, directory=False):
    info = os.lstat(path)
    wanted = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return wanted and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o077)


def _endpoint(value):
    if not isinstance(value, str):
        raise ValueError("invalid Amp connection descriptor")
    url = urllib.parse.urlsplit(value)
    try:
        port = url.port
    except ValueError as error:
        raise ValueError("invalid Amp connection descriptor") from error
    if (url.scheme, url.hostname, port, url.path) != ("http", "127.0.0.1", port, "/feedback") or not port:
        raise ValueError("invalid Amp connection descriptor")
    if url.username is not None or url.password is not None or url.query or url.fragment or url.netloc != f"127.0.0.1:{port}":
        raise ValueError("invalid Amp connection descriptor")
    return port


def _load(path):
    path = Path(path).absolute()
    if not _private(path.parent, True) or not _private(path):
        raise ValueError("unsafe Amp connection descriptor")
    raw = path.read_bytes()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("invalid Amp connection descriptor")
    port = _endpoint(data.get("url"))
    if (data.get("version") != 1 or not isinstance(data.get("token"), str) or not data["token"]
            or any(not 33 <= ord(char) <= 126 for char in data["token"])
            or not isinstance(data.get("root"), str) or not isinstance(data.get("thread"), str)
            or not data["thread"].startswith("T-") or not isinstance(data.get("title", ""), str)):
        raise ValueError("invalid Amp connection descriptor")
    return path, data, port, hashlib.sha256(raw).hexdigest()


def _request(desc, port, method, body=None, timeout=0.25):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Authorization": "Bearer " + desc["token"]}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, "/feedback", body=body, headers=headers)
        response = connection.getresponse()
        content = response.read(65537)
        return response.status, content
    finally:
        connection.close()


def discover(root):
    root = os.path.realpath(root)
    registry = Path.home() / ".cache" / "anthrodiff" / "amp"
    try:
        if not _private(registry, True):
            return []
        entries = list(registry.iterdir())
    except OSError:
        return []
    found = []
    for session_dir in entries:
        try:
            if not session_dir.name.startswith("session-") or not _private(session_dir, True):
                continue
            path, desc, port, _ = _load(session_dir / "connection.json")
            if os.path.realpath(desc["root"]) != root:
                continue
            status, body = _request(desc, port, "GET")
            if status != 200 or len(body) > 65536:
                continue
            identity = json.loads(body)
            if not isinstance(identity, dict) or any(identity.get(key) != desc[key] for key in ("version", "root", "thread")):
                continue
            found.append({"connection": str(path), "session": desc["thread"], "title": desc.get("title", "")})
        except (OSError, ValueError, TypeError, json.JSONDecodeError, http.client.HTTPException):
            continue
    return found


def _write_private(path, value, exclusive=False):
    temporary = None
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    else:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(value, file, separators=(",", ":"))
            file.flush()
            os.fsync(file.fileno())
        if temporary:
            os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def send(connection_path, expected_thread, submission_path):
    connection_path, desc, port, generation = _load(connection_path)
    canonical_root = os.path.realpath(desc["root"])
    if desc["thread"] != expected_thread or desc["root"] != canonical_root:
        raise ValueError("Amp connection identity or target does not match")
    submission_path = Path(submission_path)
    payload = json.loads(submission_path.read_text())
    if (not isinstance(payload, dict) or not isinstance(payload.get("submission_id"), str)
            or not 1 <= len(payload["submission_id"]) <= 256):
        raise ValueError("invalid submission")
    if payload.get("repository") != canonical_root:
        raise ValueError("submission repository does not match the canonical connection root")
    if "message" in payload:
        if not isinstance(payload["message"], str) or not payload["message"]:
            raise ValueError("invalid general message")
        content = payload["message"] + "\n\n" + GUIDANCE
    else:
        if not isinstance(payload.get("comments"), list) or not isinstance(payload.get("snapshots"), dict):
            raise ValueError("invalid review submission")
        content = review_prompt(payload, submission_path)
    request_id = payload["submission_id"]
    request = {"id": request_id, "content": content}
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > MAX_BODY:
        raise ValueError("Amp feedback exceeds 1 MiB")
    receipt = Path(str(submission_path) + ".amp-live-receipt.json")
    # Reuse the submission ID, and refuse changed content or connection generations.
    fingerprint = hashlib.sha256(content.encode()).hexdigest()
    identity = {"connection": str(connection_path), "generation": generation, "thread": expected_thread,
                "submission_id": payload["submission_id"], "request_id": request_id, "content_hash": fingerprint,
                "payload_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    if receipt.exists():
        if not _private(receipt):
            raise RuntimeError("unsafe Amp live receipt")
        saved = json.loads(receipt.read_text())
        if any(saved.get(key) != value for key, value in identity.items()):
            raise RuntimeError("previous Amp delivery identity or content differs; refusing retry")
        if saved.get("status") == "accepted":
            return saved["ack"]
        request["id"] = saved["request_id"]
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    else:
        saved = dict(identity, status="uncertain")
        _write_private(receipt, saved, exclusive=True)
    try:
        status, response = _request(desc, port, "GET")
        if status != 200 or len(response) > 65536:
            raise RuntimeError("Amp connection identity could not be verified; nothing sent")
        identity = json.loads(response)
        if not isinstance(identity, dict) or any(identity.get(key) != desc[key] for key in ("version", "root", "thread")):
            raise RuntimeError("Amp connection identity changed; nothing sent")
        status, _ = _request(desc, port, "POST", body, timeout=15)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError("Amp delivery is unconfirmed; retry the same submission") from error
    if status != 204:
        raise RuntimeError(f"Amp delivery is unconfirmed (HTTP {status}); retry the same submission")
    ack = {"submission_id": payload["submission_id"], "status": "accepted", "harness": "amp-live",
           "session": expected_thread, "result": "Feedback accepted by Amp."}
    saved.update(status="accepted", ack=ack)
    _write_private(receipt, saved)
    return ack


def request_reload(connection_path, expected_thread, root):
    _, desc, port, _ = _load(connection_path)
    if desc["thread"] != expected_thread or desc["root"] != os.path.realpath(root):
        raise ValueError("Amp reload target does not match this checkout and thread; nothing sent")
    status, response = _request(desc, port, "GET")
    identity = json.loads(response) if status == 200 and len(response) <= 65536 else None
    if not isinstance(identity, dict) or any(identity.get(key) != desc[key] for key in ("version", "root", "thread")):
        raise RuntimeError("Amp connection identity could not be verified; nothing sent")
    body = json.dumps({
        "id": "reload-" + uuid.uuid4().hex,
        "content": (
            "I just confirmed :harness install amp in myeditor and installed the updated anthrodiff plugin. "
            "Please call reload_plugins now to activate it. This request authorizes only that reload; "
            "do not install anything else, edit files, or change Git state. Continue any existing work afterward."
        ),
    }).encode()
    try:
        status, _ = _request(desc, port, "POST", body, timeout=15)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError("Reload request outcome is uncertain; check Amp before requesting again") from error
    if status != 204:
        raise RuntimeError(f"Reload request outcome is uncertain (HTTP {status}); check Amp before requesting again")
    return "Plugin reload request queued in Amp; this is not confirmation that plugins have reloaded."


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "discover":
        print(json.dumps(discover(sys.argv[2])))
    elif len(sys.argv) == 5 and sys.argv[1] == "send":
        print(json.dumps(send(*sys.argv[2:])))
    elif len(sys.argv) == 5 and sys.argv[1] == "reload":
        print(request_reload(*sys.argv[2:]))
    else:
        raise ValueError("usage: myeditor-amp-live discover ROOT | send CONNECTION THREAD SUBMISSION.json | reload CONNECTION THREAD ROOT")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
