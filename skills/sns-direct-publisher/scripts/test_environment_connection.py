"""Offline regression for Vault injection; no real credentials or API requests."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import private_runtime as runtime
import publisher
import runtime_guard as guard


class EnvironmentConnectionTests(unittest.TestCase):
    def setUp(self):
        self.env = {"THREADS_USER_ACCESS_TOKEN": "synthetic-vault-token+/=",
                    "THREADS_EXPECTED_USER_ID": "123", "THREADS_EXPECTED_USERNAME": "example_agent"}
        self.patch = mock.patch.dict(os.environ, self.env, clear=True)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "missing-state"

    def invoke(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = runtime.main(args)
        self.assertNotIn(self.env["THREADS_USER_ACCESS_TOKEN"], output.getvalue())
        return result, json.loads(output.getvalue())

    def test_doctor_is_local_and_reports_names_only(self):
        with mock.patch.object(publisher.APIClient, "request", side_effect=AssertionError("No network")):
            code, result = self.invoke(["doctor", "--connection", "threads"])
            self.assertEqual(code, 0)
            self.assertEqual(result["status"], "ready_for_account_check")
            self.assertEqual(result["network_requests"], 0)
            os.environ["THREADS_EXPECTED_USER_ID"] = "invalid-private-value"
            del os.environ["THREADS_EXPECTED_USERNAME"]
            code, result = self.invoke(["doctor"])
            self.assertEqual(code, 2)
            self.assertEqual(result["missing"], ["THREADS_EXPECTED_USERNAME"])
            self.assertEqual(result["invalid"], ["THREADS_EXPECTED_USER_ID"])
            self.assertNotIn("invalid-private-value", json.dumps(result))
            code, result = self.invoke(["doctor", "--connection", "x"])
            self.assertEqual(code, 2)
            self.assertEqual(len(result["missing"]), 3)

    def test_identity_check_rejects_wrong_username_without_creating_state(self):
        for name, expected in (("wrong_agent", 2), ("example_agent", 0)):
            with mock.patch.object(publisher.APIClient, "request", return_value={"id": "123", "username": name}) as api:
                code, _ = self.invoke(["run", "check", "--connection", "threads", "--state-dir", str(self.state)])
                self.assertEqual(code, expected)
                api.assert_called_once()
            self.assertFalse(self.state.exists())

    def test_plan_passes_token_in_child_environment_only(self):
        with mock.patch.object(runtime.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{}', '')) as child:
            code, _ = self.invoke(["run", "plan", "--connection", "threads", "--state-dir", str(self.state)])
        self.assertEqual(code, 0)
        self.assertEqual(child.call_args.kwargs["env"]["THREADS_USER_ACCESS_TOKEN"], self.env["THREADS_USER_ACCESS_TOKEN"])
        self.assertNotIn(self.env["THREADS_USER_ACCESS_TOKEN"], str(child.call_args.args))
        self.assertFalse(self.state.exists())

    def test_missing_state_blocks_publish_metrics_and_guard_before_network(self):
        with mock.patch.object(publisher.APIClient, "request", side_effect=AssertionError("No network")), \
                mock.patch.object(runtime.subprocess, "run", side_effect=AssertionError("No subprocess")):
            for command in ("publish", "metrics"):
                code, _ = self.invoke(["run", command, "--connection", "threads", "--state-dir", str(self.state)])
                self.assertEqual(code, 2)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(guard.main(["--connection", "threads", "--state-dir", str(self.state),
                                             "--runtime-id", "example-runtime"]), 2)
        self.assertFalse(self.state.exists())

    def test_environment_and_file_sources_are_mutually_exclusive(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            runtime.main(["run", "check", "--connection", "threads", "--config", "unused.json",
                          "--state-dir", str(self.state)])

    def test_instagram_requires_explicit_mode(self):
        for suffix in ("USER_ACCESS_TOKEN", "EXPECTED_USER_ID", "EXPECTED_USERNAME"):
            os.environ["INSTAGRAM_" + suffix] = self.env["THREADS_" + suffix]
        code, result = self.invoke(["doctor", "--connection", "instagram"])
        self.assertEqual(code, 2)
        self.assertEqual(result["missing"], ["INSTAGRAM_LOGIN_MODE"])
        os.environ["INSTAGRAM_LOGIN_MODE"] = "unified"
        code, _ = self.invoke(["doctor", "--connection", "instagram"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
