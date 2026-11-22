import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import private_runtime as runtime
import publisher as p
import runtime_guard as guard


TOKENS = {"threads": "synthetic-threads-guard-token+/=", "instagram": "synthetic-instagram-guard-token+/="}


class RuntimeGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        self.state = self.root / "state"
        self.paths = {}
        for platform, account in (("threads", "123"), ("instagram", "456")):
            path = self.root / (platform + ".json")
            runtime.store_config(path, {"version": 1, "platform": platform, "access_token": TOKENS[platform],
                "expected_username": "example_agent", "expected_user_id": account,
                "instagram_login_mode": "unified" if platform == "instagram" else None,
                "expiry_status": "unknown", "expires_at": None})
            self.paths[platform] = str(path)
        self.calls = []

    def api(self, client, method, path, **kwargs):
        self.calls.append((client.platform, method, path, kwargs))
        host = "threads.com/@example_agent/post/ABC" if client.platform == "threads" else "www.instagram.com/reel/ABC/"
        return {"data": [{"id": "789", "permalink": "https://" + host,
                          "timestamp": "2026-11-22T09:00:00+0000", "access_token": TOKENS[client.platform]}],
                "paging": {"next": "https://invalid.test/?access_token=" + TOKENS[client.platform]}}

    def invoke(self, initialize=False, runtime_id="example-runtime-v1", state=None, identity_error=None):
        output = io.StringIO()
        args = ["--threads-config", self.paths["threads"], "--instagram-config", self.paths["instagram"],
                "--state-dir", str(state or self.state), "--runtime-id", runtime_id]
        if initialize:
            args.append("--initialize")
        identity = (lambda *a, **kw: a[3]) if identity_error is None else identity_error
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                mock.patch.object(runtime, "verify_identity", side_effect=identity), \
                mock.patch.object(guard, "verify_ffprobe"), \
                mock.patch.object(p.APIClient, "request", lambda client, *a, **kw: self.api(client, *a, **kw)):
            code = guard.main(args)
        rendered = output.getvalue()
        for token in TOKENS.values():
            for variant in runtime.secret_variants(token):
                self.assertNotIn(variant, rendered)
        self.assertNotIn("invalid.test", rendered)
        return code, json.loads(rendered)

    def snapshot(self):
        return {p.name: (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_mode)
                for p in self.state.iterdir() if p.is_file()}

    def test_initialize_and_recurring_check_preserve_existing_state(self):
        code, output = self.invoke(initialize=True)
        self.assertEqual(code, 0)
        self.assertTrue(output["initialized"])
        self.assertEqual(output["ledger_jobs"], 0)
        self.assertFalse(output["persistence_guaranteed"])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
        for name in ("runtime.json", "publisher.sqlite3"):
            self.assertEqual((self.state / name).stat().st_mode & 0o777, 0o600)
        before = self.snapshot()
        code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertFalse(output["initialized"])
        self.assertEqual(before, self.snapshot())
        self.assertEqual({call[2] for call in self.calls}, {"/123/threads", "/456/media"})
        self.assertTrue(all(call[1] == "GET" for call in self.calls))
        code, _ = self.invoke(initialize=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(before, self.snapshot())

    def test_missing_or_existing_empty_state_is_never_implicitly_created(self):
        code, _ = self.invoke()
        self.assertNotEqual(code, 0)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.calls, [])
        self.state.mkdir(mode=0o700)
        code, _ = self.invoke(initialize=True)
        self.assertNotEqual(code, 0)
        self.assertEqual(list(self.state.iterdir()), [])
        self.assertEqual(self.calls, [])

    def test_symlink_permission_type_and_replaced_ledger_are_blocked(self):
        self.invoke(initialize=True)
        alias = self.root / "alias"
        alias.symlink_to(self.state, target_is_directory=True)
        self.assertNotEqual(self.invoke(state=alias)[0], 0)
        self.assertNotEqual(self.invoke(state=alias / "child")[0], 0)
        marker = self.state / "runtime.json"
        marker.chmod(0o644)
        self.assertNotEqual(self.invoke()[0], 0)
        marker.chmod(0o600)
        db = self.state / "publisher.sqlite3"
        replacement = self.state / "replacement"
        replacement.write_bytes(db.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(db)
        self.assertNotEqual(self.invoke()[0], 0)
        db.unlink()
        db.mkdir(mode=0o600)
        self.assertNotEqual(self.invoke()[0], 0)

    def test_runtime_account_and_failed_identity_do_not_initialize_or_rebind(self):
        error = RuntimeError("credential=" + TOKENS["threads"])
        self.assertNotEqual(self.invoke(initialize=True, identity_error=error)[0], 0)
        self.assertFalse(self.state.exists())
        self.invoke(initialize=True)
        before = self.snapshot()
        self.assertNotEqual(self.invoke(runtime_id="different-runtime")[0], 0)
        config = Path(self.paths["threads"])
        data = json.loads(config.read_text())
        data["expected_user_id"] = "999"
        config.write_text(json.dumps(data))
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(before, self.snapshot())

    def test_corrupt_and_wal_ledger_fail_without_sidecar_creation(self):
        self.invoke(initialize=True)
        db = self.state / "publisher.sqlite3"
        original = db.read_bytes()
        db.write_bytes(b"not a sqlite database")
        self.assertNotEqual(self.invoke()[0], 0)
        db.write_bytes(original)
        conn = sqlite3.connect(db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        before = self.snapshot()
        self.assertNotEqual(self.invoke()[0], 0)
        self.assertEqual(before, self.snapshot())

    def test_ffprobe_and_owned_response_sanitization(self):
        with mock.patch.object(guard.shutil, "which", return_value=None):
            with self.assertRaises(p.SafeError):
                guard.verify_ffprobe()
        with mock.patch.object(guard.shutil, "which", return_value="/synthetic/ffprobe"), \
                mock.patch.object(guard.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="ffprobe version synthetic\n")) as command:
            guard.verify_ffprobe()
            self.assertEqual(command.call_args.args[0], ["/synthetic/ffprobe", "-version"])
        for value in ("https://invalid.test/post/123", "https://threads.com/p?access_token=" + TOKENS["threads"]):
            with self.assertRaises(p.SafeError):
                guard.sanitized_posts([{"id": "123", "permalink": value}], "threads")
        with self.assertRaises(p.SafeError):
            guard.sanitized_posts([{"id": TOKENS["threads"]}], "threads")


if __name__ == "__main__":
    unittest.main()
