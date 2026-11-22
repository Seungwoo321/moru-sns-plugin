"""Offline private-runtime checks; only synthetic credentials and fake account clients."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

import private_runtime as r


TOKEN = "synthetic-token-for-offline-test+/="


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = iter(responses), []

    def request(self, method, path, payload=None, params=None):
        self.calls.append((method, path, params))
        return next(self.responses)


class PrivateRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.config = self.base / "private" / "threads.json"

    def tearDown(self):
        self.temp.cleanup()

    def configure_args(self):
        return ["configure", "--platform", "threads", "--expected-username", "example_agent",
                "--config", str(self.config)]

    def fixture(self):
        return {"version": 1, "platform": "threads", "expected_username": "example_agent",
                "expected_user_id": "123", "instagram_login_mode": None, "access_token": TOKEN,
                "expiry_status": "unknown", "expires_at": None}

    def test_configure_checks_account_then_saves_private_file_without_token_output(self):
        output, errors = io.StringIO(), io.StringIO()
        for username, expected_code in (("another_agent", 2), ("example_agent", 0)):
            client = FakeClient({"id": "123", "username": username})
            with mock.patch.object(r.sys.stdin, "isatty", return_value=True), \
                    mock.patch.object(r.getpass, "getpass", return_value=TOKEN), \
                    mock.patch.object(r.p, "APIClient", return_value=client), \
                    contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                self.assertEqual(r.main(self.configure_args()), expected_code)
            self.assertEqual(client.calls, [("GET", "/me", {"fields": "id,username"})])
            self.assertEqual(self.config.exists(), expected_code == 0)
        self.assertEqual(stat.S_IMODE(self.config.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o600)
        self.assertEqual(self.config.stat().st_nlink, 1)
        self.assertEqual(r.load_config(str(self.config))["access_token"], TOKEN)
        self.assertEqual(list(self.config.parent.iterdir()), [self.config])
        self.assertNotIn(TOKEN, output.getvalue() + errors.getvalue())
        self.assertIn('"expiry_status": "unknown"', output.getvalue())
        with mock.patch.object(r.getpass, "getpass", side_effect=AssertionError("Do not overwrite")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.main(self.configure_args()), 2)

    def test_run_pins_identity_uses_environment_only_and_redacts_child_output(self):
        r.store_config(self.config, self.fixture())
        output, errors = io.StringIO(), io.StringIO()
        result = mock.Mock(returncode=2, stdout="safe " + TOKEN, stderr=r.urllib.parse.quote(TOKEN, safe=""))
        args = ["run", "plan", "--config", str(self.config), "--state-dir", str(self.base / "state"),
                "--job-id", "one", "--text-file", str(self.base / "text.txt")]
        with mock.patch.dict(os.environ, {"X_USER_ACCESS_TOKEN": "unrelated-synthetic", "THREADS_EXPECTED_USER_ID": "999"}, clear=True), \
                mock.patch.object(r.subprocess, "run", return_value=result) as execute, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(r.main(args), 2)
        command = execute.call_args.args[0]
        env = execute.call_args.kwargs["env"]
        self.assertNotIn(TOKEN, " ".join(command))
        self.assertEqual(command[1], "-I")
        self.assertEqual(command[command.index("--platform") + 1], "threads")
        self.assertEqual(env["THREADS_USER_ACCESS_TOKEN"], TOKEN)
        self.assertEqual(env["THREADS_EXPECTED_USER_ID"], "123")
        self.assertNotIn("X_USER_ACCESS_TOKEN", env)
        self.assertNotIn(TOKEN, output.getvalue() + errors.getvalue())
        self.assertIn("[REDACTED]", output.getvalue())
        with mock.patch.object(r.subprocess, "run", side_effect=AssertionError("No secret argv")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.main(["run", "check", "--config", str(self.config),
                                     "--state-dir", str(self.base / TOKEN)]), 2)

    def test_insecure_paths_and_unified_mismatch_fail_closed(self):
        r.store_config(self.config, self.fixture())
        self.config.chmod(0o644)
        with self.assertRaises(r.p.SafeError):
            r.load_config(str(self.config))
        self.config.chmod(0o600)
        link = self.base / "linked"
        link.symlink_to(self.config.parent, target_is_directory=True)
        with self.assertRaises(r.p.SafeError):
            r.load_config(str(link / self.config.name))
        (self.base / ".git").mkdir()
        with self.assertRaises(r.p.SafeError):
            r.load_config(str(self.config))
        (self.base / ".git").rmdir()
        for username, should_match in (("other", False), ("example_agent", True)):
            client = FakeClient({"data": [{"id": "123", "username": username}]})
            with mock.patch.object(r.p, "APIClient", return_value=client):
                if should_match:
                    self.assertEqual(r.verify_identity("instagram", TOKEN, "example_agent", "123", "unified"), "123")
                else:
                    with self.assertRaises(r.p.SafeError):
                        r.verify_identity("instagram", TOKEN, "example_agent", "123", "unified")
            self.assertEqual(client.calls, [("GET", "/me/assigned_instagram_accounts", {"fields": "id,username"})])
        with mock.patch.object(r.sys.stdin, "isatty", return_value=False), \
                mock.patch.object(r.getpass, "getpass", side_effect=AssertionError("No echo fallback")), \
                contextlib.redirect_stdout(io.StringIO()):
            args = self.configure_args()
            args[-1] = str(self.config.parent / "second.json")
            self.assertEqual(r.main(args), 2)


if __name__ == "__main__":
    unittest.main()
