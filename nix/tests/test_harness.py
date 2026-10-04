import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("receiver", sys.argv.pop(1))
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


THREAD = "T-00000000-1111-2222-3333-444444444444"


class HarnessReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.payload = Path(self.temp.name) / "batch.json"
        self.payload.write_text(
            json.dumps(
                {
                    "submission_id": "batch-1",
                    "repository": self.temp.name,
                    "comments": [
                        {
                            "file": "file with spaces.go",
                            "side": "new",
                            "line": 177,
                            "text": "Use sentinel errors.",
                            "snapshot_id": "s",
                        }
                    ],
                    "snapshots": {
                        "s": {"group": "untracked", "new": "UNRELATED SOURCE"}
                    },
                    "snapshot_status": {"s": "current"},
                }
            )
        )
        self.receipt = Path(str(self.payload) + ".claude-receipt.json")

    def invoke(self, harness="claude", session="explicit-session"):
        output = io.StringIO()
        with (
            patch.object(
                sys, "argv", ["receiver", harness, session, str(self.payload)]
            ),
            contextlib.redirect_stdout(output),
        ):
            receiver.main()
        return json.loads(output.getvalue())

    def test_completed_receipt_prevents_double_execution(self):
        response = subprocess.CompletedProcess(
            [], 0, '{"subtype":"success","result":"fixed","is_error":false}', ""
        )
        with patch.object(receiver.subprocess, "run", return_value=response) as run:
            first = self.invoke()
            second = self.invoke()
        self.assertEqual(first, second)
        self.assertEqual("batch-1", first["submission_id"])
        self.assertEqual("completed", first["status"])
        self.assertEqual(1, run.call_count)
        self.assertEqual(
            [
                "claude",
                "--print",
                "--resume",
                "explicit-session",
                "--output-format",
                "json",
            ],
            run.call_args.args[0],
        )
        self.assertIn(
            f"{self.temp.name}/file with spaces.go:177 (new; untracked)\nUse sentinel errors.",
            run.call_args.kwargs["input"],
        )
        self.assertNotIn("UNRELATED SOURCE", run.call_args.kwargs["input"])
        self.assertEqual(0o600, self.receipt.stat().st_mode & 0o777)

    def test_review_prompt_ranges_and_note_order(self):
        payload = {
            "repository": "/checkout with spaces",
            "comments": [
                {
                    "file": "old.go",
                    "side": "old",
                    "line": 8,
                    "line_end": 12,
                    "text": "First note\nPreserve the second paragraph.",
                    "snapshot_id": "s",
                },
                {
                    "file": "α.go",
                    "side": "new",
                    "line": 21,
                    "line_end": 22,
                    "text": "Second note",
                    "snapshot_id": "s",
                    "selection": {
                        "kind": "character",
                        "spans": [
                            {"line": 21, "start_byte": 5, "end_byte": 18},
                            {"line": 22, "start_byte": 1, "end_byte": 9},
                        ],
                    },
                },
            ],
            "snapshots": {"s": {"group": "staged"}},
            "snapshot_status": {"s": "changed"},
        }
        prompt = receiver.review_prompt(payload, self.payload)
        sections = prompt.split("\n\n")
        self.assertEqual(
            "Address these review annotations. Locations refer to the reviewed versions.",
            sections[0],
        )
        self.assertEqual(
            "/checkout with spaces/old.go:8-12 (old; staged; snapshot changed)\nFirst note\nPreserve the second paragraph.",
            sections[1],
        )
        self.assertEqual(
            "/checkout with spaces/α.go:21:5-22:9 (new; staged; snapshot changed; character selection)\nSecond note",
            sections[2],
        )
        self.assertTrue(sections[3].startswith(receiver.GUIDANCE))
        self.assertTrue(sections[3].endswith(str(self.payload.absolute())))

    def test_uncertain_delivery_is_not_reexecuted(self):
        response = subprocess.CompletedProcess([], 1, "", "connection lost")
        with patch.object(receiver.subprocess, "run", return_value=response) as run:
            with self.assertRaisesRegex(RuntimeError, "connection lost"):
                self.invoke()
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                self.invoke()
        self.assertEqual(1, run.call_count)

    def test_missing_executable_can_be_retried(self):
        with (
            patch.object(receiver.subprocess, "run", side_effect=FileNotFoundError),
            self.assertRaises(FileNotFoundError),
        ):
            self.invoke()
        self.assertFalse(self.receipt.exists())

    def test_general_message_does_not_send_review_context(self):
        self.payload.write_text(json.dumps({"submission_id": "message-1", "message": "Explain the design"}))
        response = subprocess.CompletedProcess([], 0, '{"subtype":"success","result":"explained","is_error":false}', "")
        with patch.object(receiver.subprocess, "run", return_value=response) as run:
            self.assertEqual("completed", self.invoke()["status"])
        prompt = run.call_args.kwargs["input"]
        self.assertTrue(prompt.startswith("Explain the design\n\n"))
        self.assertNotIn("code-review", prompt)
        self.assertIn("user owns staging", prompt)

    def amp_output(self, **overrides):
        result = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": THREAD,
            "result": "Fixed expiry; tests passed.",
        }
        result.update(overrides)
        return (
            "\n".join(
                json.dumps(event)
                for event in [
                    {"type": "system", "subtype": "init", "session_id": THREAD},
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "Working"}]},
                    },
                    result,
                ]
            )
            + "\n"
        )

    def test_amp_continues_explicit_thread_and_deduplicates(self):
        response = subprocess.CompletedProcess(
            [], 0, self.amp_output(permission_denials=["shell_command"]), ""
        )
        with patch.object(receiver.subprocess, "run", return_value=response) as run:
            first = self.invoke("amp", THREAD)
            self.assertEqual(first, self.invoke("amp", THREAD))
        self.assertEqual(1, run.call_count)
        self.assertEqual(
            [
                "amp",
                "threads",
                "continue",
                THREAD,
                "--execute",
                "--stream-json",
                "--no-ide",
            ],
            run.call_args.args[0],
        )
        self.assertEqual("amp", first["harness"])
        self.assertEqual("batch-1", first["submission_id"])
        self.assertEqual("Fixed expiry; tests passed.", first["result"])
        self.assertEqual(["shell_command"], first["permission_denials"])
        self.assertIn(
            f"{self.temp.name}/file with spaces.go:177 (new; untracked)\nUse sentinel errors.",
            run.call_args.kwargs["input"],
        )
        self.assertNotIn("UNRELATED SOURCE", run.call_args.kwargs["input"])
        self.assertEqual(
            0o600, Path(str(self.payload) + ".amp-receipt.json").stat().st_mode & 0o777
        )

    def test_amp_zero_exit_error_is_not_acknowledged_or_retried(self):
        output = self.amp_output(
            subtype="error_during_execution", is_error=True, error="permission denied"
        )
        with patch.object(
            receiver.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ) as run:
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                self.invoke("amp", THREAD)
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                self.invoke("amp", THREAD)
        self.assertEqual(1, run.call_count)

    def test_amp_requires_final_result_for_matching_thread(self):
        for output in [
            '{"type":"assistant"}\n',
            self.amp_output(session_id="another-thread"),
            self.amp_output(is_error=None),
            self.amp_output() + "{}\n",
            "",
        ]:
            with self.subTest(output=output), self.assertRaises(RuntimeError):
                receiver.parse_response("amp", THREAD, output)
        with self.assertRaises(ValueError):
            receiver.parse_response("amp", THREAD, "not json")

    def test_unknown_harness_and_implicit_targets_rejected_before_execution(self):
        for harness, session in [
            ("aider", "session"),
            ("amp", "--last"),
            ("amp", "T-short"),
            ("amp", ""),
            ("claude", "--continue"),
        ]:
            with self.subTest(harness=harness, session=session):
                with (
                    patch.object(receiver.subprocess, "run") as run,
                    self.assertRaises(ValueError),
                ):
                    self.invoke(harness, session)
                run.assert_not_called()

    def test_receipt_mismatch_cannot_acknowledge_different_session_or_payload(self):
        self.receipt.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "session": "explicit-session",
                    "submission_id": "batch-1",
                }
            )
        )
        with patch.object(receiver.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                self.invoke(session="another-session")
            self.payload.write_text(json.dumps({"submission_id": "batch-2"}))
            with self.assertRaisesRegex(RuntimeError, "uncertain"):
                self.invoke()
            run.assert_not_called()


unittest.main()
