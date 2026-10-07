"""Shared loopback bridge for rediff's Amp and oh-my-pi extensions."""

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

from harness import feedback_prompt

MAX_BODY = 1024 * 1024
LABELS = {"amp": "Amp", "omp": "oh-my-pi"}


def _private(path, directory=False):
    info = os.lstat(path)
    wanted = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    return wanted and not stat.S_ISLNK(info.st_mode) and info.st_uid == os.getuid() and not (info.st_mode & 0o077)


def _endpoint(value):
    if not isinstance(value, str):
        raise ValueError("invalid harness connection descriptor")
    url = urllib.parse.urlsplit(value)
    try:
        port = url.port
    except ValueError as error:
        raise ValueError("invalid harness connection descriptor") from error
    if (url.scheme, url.hostname, port, url.path) != ("http", "127.0.0.1", port, "/feedback") or not port:
        raise ValueError("invalid harness connection descriptor")
    if url.username is not None or url.password is not None or url.query or url.fragment or url.netloc != f"127.0.0.1:{port}":
        raise ValueError("invalid harness connection descriptor")
    return port


def _load(path, provider="amp"):
    path = Path(path).absolute()
    try:
        if not _private(path.parent, True) or not _private(path):
            raise ValueError("unsafe harness connection descriptor")
        raw = path.read_bytes()
    except FileNotFoundError as error:
        raise ValueError(f"This {LABELS[provider]} connection has closed. Run :harness connect {provider} to select a live session. Nothing was sent by this attempt.") from error
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("invalid harness connection descriptor")
    port = _endpoint(data.get("url"))
    if (data.get("provider", "amp") != provider
            or data.get("version") != 1 or not isinstance(data.get("token"), str) or not data["token"]
            or any(not 33 <= ord(char) <= 126 for char in data["token"])
            or not isinstance(data.get("root"), str) or not isinstance(data.get("thread"), str)
            or not 1 <= len(data["thread"]) <= 256
            or (provider == "amp" and not data["thread"].startswith("T-"))
            or not isinstance(data.get("title", ""), str)
            or ("capabilities" in data and
                (not isinstance(data["capabilities"], list)
                 or any(not isinstance(value, str) for value in data["capabilities"])))):
        raise ValueError("invalid harness connection descriptor")
    if provider == "omp":
        uuid.UUID(data["thread"])
    return path, data, port, hashlib.sha256(raw).hexdigest()


def _request(desc, port, method, body=None, timeout=0.25, path="/feedback"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Authorization": "Bearer " + desc["token"]}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        content = response.read(65537)
        return response.status, content
    finally:
        connection.close()


def discover(root, provider="amp"):
    root = os.path.realpath(root) if root is not None else None
    registry = Path.home() / ".cache" / "rediff" / provider
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
            path, desc, port, _ = _load(session_dir / "connection.json", provider)
            if root is not None and os.path.realpath(desc["root"]) != root:
                continue
            status, body = _request(desc, port, "GET")
            if status != 200 or len(body) > 65536:
                continue
            identity = json.loads(body)
            if not isinstance(identity, dict) or any(identity.get(key) != desc[key] for key in ("version", "root", "thread")):
                continue
            entry = {"root": desc["root"], "connection": str(path), "session": desc["thread"],
                     "title": desc.get("title", "")}
            if "capabilities" in desc:
                entry["capabilities"] = desc["capabilities"]
            found.append(entry)
        except (OSError, ValueError, TypeError, json.JSONDecodeError, http.client.HTTPException):
            continue
    return found


def watch(connection_path, expected_thread, provider="amp"):
    """Print authenticated activity snapshots until the stream disconnects."""
    label = LABELS[provider]
    _, desc, port, _ = _load(connection_path, provider)
    if desc["thread"] != expected_thread:
        raise ValueError(f"{label} connection identity does not match")
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("GET", "/events", headers={"Authorization": "Bearer " + desc["token"]})
        response = connection.getresponse()
        if response.status != 200:
            response.read(65537)
            raise RuntimeError(f"{label} activity stream failed (HTTP {response.status})")
        last_sequence = -1
        last_revision = 0
        while True:
            line = response.readline(65538)
            if not line:
                raise RuntimeError(f"{label} activity stream disconnected")
            if len(line) > 65537 or (len(line) == 65537 and not line.endswith(b"\n")):
                raise RuntimeError(f"invalid {label} activity snapshot size")
            if not line.strip():
                continue
            snapshot = json.loads(line)
            valid_states = {"idle", "running", "awaiting-approval", "error", "unknown"}
            sequence = snapshot.get("sequence") if isinstance(snapshot, dict) else None
            revision = snapshot.get("files_revision", 0) if isinstance(snapshot, dict) else None
            if (not isinstance(snapshot, dict) or snapshot.get("version") != 1
                    or snapshot.get("root") != desc["root"] or snapshot.get("thread") != desc["thread"]
                    or not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0
                    or sequence <= last_sequence or snapshot.get("state") not in valid_states
                    or not isinstance(revision, int) or isinstance(revision, bool) or revision < last_revision
                    or not isinstance(snapshot.get("title"), str)
                    or not (snapshot.get("tool") is None or isinstance(snapshot.get("tool"), str))):
                raise RuntimeError(f"invalid {label} activity snapshot")
            last_sequence = sequence
            last_revision = revision
            print(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")), flush=True)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"{label} activity stream disconnected") from error
    finally:
        connection.close()


def _validate_routing(payload, desc, provider="amp"):
    source = payload.get("repository")
    if not isinstance(source, str) or source != os.path.realpath(source):
        raise ValueError("submission repository is not canonical")
    target = os.path.realpath(desc["root"])
    sender = payload.get("sender")
    recipient = payload.get("recipient")
    if sender is not None:
        if (not isinstance(sender, dict) or not isinstance(sender.get("instance"), str)
                or not sender["instance"] or not isinstance(sender.get("pid"), int)
                or isinstance(sender.get("pid"), bool) or sender["pid"] <= 0
                or not isinstance(sender.get("directory"), str) or not os.path.isabs(sender["directory"])
                or sender.get("repository") != source or sender.get("repository") != os.path.realpath(sender.get("repository", ""))
                or sender.get("app") not in ("rediff", "nvim")):
            raise ValueError("malformed sender metadata")
    if recipient is not None:
        if (not isinstance(recipient, dict) or recipient != {"provider": provider, "id": desc["thread"],
                                                              "repository": target}):
            raise ValueError("mismatched recipient metadata")
    cross_root = source != target
    if cross_root:
        if "message" not in payload or "comments" in payload or "snapshots" in payload:
            raise ValueError("cross-root annotation submission repository is refused")
        if sender is None or recipient is None:
            raise ValueError("cross-root messages require sender and recipient metadata")
    elif recipient is not None and recipient["repository"] != target:
        raise ValueError("mismatched recipient metadata")


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


def send(connection_path, expected_thread, submission_path, provider="amp"):
    label = LABELS[provider]
    connection_path, desc, port, generation = _load(connection_path, provider)
    canonical_root = os.path.realpath(desc["root"])
    if desc["thread"] != expected_thread or desc["root"] != canonical_root:
        raise ValueError(f"{label} connection identity or target does not match")
    submission_path = Path(submission_path)
    payload = json.loads(submission_path.read_text())
    if (not isinstance(payload, dict) or not isinstance(payload.get("submission_id"), str)
            or not 1 <= len(payload["submission_id"]) <= 256):
        raise ValueError("invalid submission")
    _validate_routing(payload, desc, provider)
    if "message" in payload:
        if not isinstance(payload["message"], str) or not payload["message"]:
            raise ValueError("invalid general message")
    else:
        if not isinstance(payload.get("comments"), list) or not isinstance(payload.get("snapshots"), dict):
            raise ValueError("invalid review submission")
    content = feedback_prompt(payload, submission_path)
    request_id = payload["submission_id"]
    request = {"id": request_id, "content": content}
    body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode()
    if len(body) > MAX_BODY:
        raise ValueError(f"{label} feedback exceeds 1 MiB")
    receipt = Path(str(submission_path) + f".{provider}-live-receipt.json")
    # Reuse the submission ID, and refuse changed content or connection generations.
    fingerprint = hashlib.sha256(content.encode()).hexdigest()
    identity = {"connection": str(connection_path), "generation": generation, "thread": expected_thread,
                "submission_id": payload["submission_id"], "request_id": request_id, "content_hash": fingerprint,
                "payload_hash": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    if receipt.exists():
        if not _private(receipt):
            raise RuntimeError(f"unsafe {label} live receipt")
        saved = json.loads(receipt.read_text())
        if any(saved.get(key) != value for key, value in identity.items()):
            raise RuntimeError(f"previous {label} delivery identity or content differs; refusing retry")
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
            raise RuntimeError(f"{label} connection identity could not be verified; nothing sent")
        identity = json.loads(response)
        if not isinstance(identity, dict) or any(identity.get(key) != desc[key] for key in ("version", "root", "thread")):
            raise RuntimeError(f"{label} connection identity changed; nothing sent")
        status, _ = _request(desc, port, "POST", body, timeout=15)
    except (OSError, http.client.HTTPException) as error:
        raise RuntimeError(f"{label} delivery is unconfirmed; retry the same submission") from error
    if status != 204:
        raise RuntimeError(f"{label} delivery is unconfirmed (HTTP {status}); retry the same submission")
    ack = {"submission_id": payload["submission_id"], "status": "accepted", "harness": f"{provider}-live",
           "session": expected_thread, "result": f"Feedback handed to {LABELS[provider]}."}
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
            "I just confirmed :harness install amp in rediff and installed the updated readiff plugin. "
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
    provider = "amp"
    if len(sys.argv) >= 3 and sys.argv[1] == "--provider":
        provider = sys.argv[2]
        del sys.argv[1:3]
    if provider not in (*LABELS, "all"):
        raise ValueError("unknown live harness provider")
    if len(sys.argv) == 3 and sys.argv[1] == "discover":
        root = None if sys.argv[2] == "--all" else sys.argv[2]
        if provider == "all":
            print(json.dumps([dict(row, provider=name) for name in LABELS for row in discover(root, name)]))
        else:
            print(json.dumps(discover(root, provider)))
    elif len(sys.argv) == 4 and sys.argv[1] == "watch":
        if provider not in LABELS:
            raise ValueError("select a live harness provider")
        watch(*sys.argv[2:], provider=provider)
    elif len(sys.argv) == 5 and sys.argv[1] == "send":
        if provider not in LABELS:
            raise ValueError("select a live harness provider")
        print(json.dumps(send(*sys.argv[2:], provider=provider)))
    elif provider == "amp" and len(sys.argv) == 5 and sys.argv[1] == "reload":
        print(request_reload(*sys.argv[2:]))
    else:
        raise ValueError("usage: [--provider amp|omp|all] discover ROOT|--all | watch CONNECTION SESSION | send CONNECTION SESSION SUBMISSION.json | reload CONNECTION THREAD ROOT (Amp only)")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
