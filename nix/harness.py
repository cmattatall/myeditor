"""Explicit-session Amp and Claude Code receivers for myeditor feedback."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path


def command(harness, session):
    if not session or session.startswith("-"):
        raise ValueError("An explicit session ID is required")
    if harness == "amp":
        if not re.fullmatch(
            r"T-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", session
        ):
            raise ValueError("Amp requires an explicit T-uuid thread ID")
        return [
            "amp",
            "threads",
            "continue",
            session,
            "--execute",
            "--stream-json",
            "--no-ide",
        ]
    if harness == "claude":
        return ["claude", "--print", "--resume", session, "--output-format", "json"]
    raise ValueError("Supported built-in harnesses: amp, claude")


def parse_response(harness, session, output):
    if harness == "amp":
        messages = [json.loads(line) for line in output.splitlines() if line.strip()]
        if (
            not messages
            or not isinstance(messages[-1], dict)
            or messages[-1].get("type") != "result"
        ):
            raise RuntimeError(
                "Amp exited without a final result; delivery is uncertain"
            )
        response = messages[-1]
        if response.get("session_id") != session:
            raise RuntimeError(
                "Amp responded for a different thread; delivery is uncertain"
            )
    else:
        response = json.loads(output)
    if not isinstance(response, dict):
        raise TypeError("Harness returned an invalid result; delivery is uncertain")
    if response.get("is_error") or response.get("subtype") != "success":
        raise RuntimeError(
            response.get("error")
            or response.get("result")
            or "Harness reported an error"
        )
    if response.get("is_error") is not False or not isinstance(
        response.get("result"), str
    ):
        raise RuntimeError("Harness did not confirm success; delivery is uncertain")
    return response


def main():
    if len(sys.argv) != 4:
        raise ValueError(
            "usage: myeditor-harness {amp|claude} SESSION_ID SUBMISSION.json"
        )
    harness, session, path = sys.argv[1:]
    argv = command(harness, session)
    payload = json.loads(Path(path).read_text())
    receipt = Path(path + f".{harness}-receipt.json")
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if (
            saved.get("status") == "completed"
            and saved.get("session") == session
            and saved.get("submission_id") == payload["submission_id"]
        ):
            print(json.dumps(saved))
            return
        raise RuntimeError(
            f"Previous {harness} delivery is uncertain. Check session {session} before "
            f"removing {receipt} and retrying."
        )
    # Claim before starting the agent. A crash must not silently execute twice.
    fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(
            {
                "status": "running",
                "session": session,
                "submission_id": payload["submission_id"],
            },
            file,
        )
        file.flush()
        os.fsync(file.fileno())
    prompt = (
        "Address this human code-review feedback in the current repository. "
        "The payload contains immutable reviewed content; check current files before "
        "editing. Do not stage, commit, or push. Report changes and tests.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    if "message" in payload:
        prompt = payload["message"] + (
            "\n\nThe user owns staging. Do not stage, unstage, reset, commit, or "
            "push without asking. Check current files before editing."
        )
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            text=True,
            capture_output=True,
            check=False,
        )
    except OSError:
        receipt.unlink()  # Process did not start, so retry is safe.
        raise
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or f"{harness} failed")
    response = parse_response(harness, session, result.stdout)
    ack = {
        "submission_id": payload["submission_id"],
        "status": "completed",
        "harness": harness,
        "session": session,
        "result": response["result"],
        "permission_denials": response.get("permission_denials", []),
    }
    temp = receipt.with_suffix(".tmp")
    with open(temp, "w", opener=lambda p, flags: os.open(p, flags, 0o600)) as file:
        json.dump(ack, file)
        file.flush()
        os.fsync(file.fileno())
    temp.replace(receipt)
    print(json.dumps(ack))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
