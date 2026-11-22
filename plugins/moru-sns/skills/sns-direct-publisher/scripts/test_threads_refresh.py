import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import private_runtime as runtime
import publisher as p
import threads_refresh as refresh


OLD = "synthetic-old-refresh-token+/="
NEW = "synthetic-new-refresh-token+/="
NOW = 1800000000


class ThreadsRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        os.chmod(self.directory, 0o700)
        self.path = self.directory / "threads.json"
        self.config = {"version": 1, "platform": "threads", "access_token": OLD,
            "expected_username": "example_agent", "expected_user_id": "123", "instagram_login_mode": None,
            "expiry_status": "unknown", "expires_at": None}
        runtime.store_config(self.path, self.config)
        self.metadata = {"is_valid": True, "type": "USER", "issued_at": NOW - 54 * refresh.DAY,
            "expires_at": NOW + 6 * refresh.DAY, "user_id": "123", "app_id": "456",
            "scopes": ["threads_basic", "threads_content_publish"]}
        self.calls = []

    def fake(self, client, method, path, **kwargs):
        self.calls.append((client.base, client.token, method, path, kwargs))
        if path == "/debug_token":
            return {"data": self.metadata}
        if path == "/refresh_access_token":
            return {"access_token": NEW, "token_type": "bearer", "expires_in": 60 * refresh.DAY - 30}
        raise AssertionError("Unexpected endpoint")

    def invoke(self, execute=True):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), \
                mock.patch.object(refresh.time, "time", return_value=NOW):
            code = refresh.main(["--config", str(self.path)] + (["--execute"] if execute else []))
        text = output.getvalue()
        for token in (OLD, NEW):
            for variant in runtime.secret_variants(token):
                self.assertNotIn(variant, text)
        self.assertNotIn("https://", text)
        return code, json.loads(text)

    def test_refresh_verifies_identity_and_replaces_protected_config(self):
        with mock.patch.object(p.APIClient, "request", lambda client, *a, **kw: self.fake(client, *a, **kw)), \
                mock.patch.object(runtime, "verify_identity", return_value="123") as identity:
            code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "refreshed")
        identity.assert_called_once_with("threads", NEW, "example_agent", "123")
        saved = runtime.load_config(str(self.path))
        self.assertEqual(saved["access_token"], NEW)
        self.assertEqual(saved["expiry_status"], "unknown")
        self.assertEqual(saved["threads_refresh"]["last_refreshed_at"], NOW)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.stat().st_nlink, 1)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.calls[1], ("https://graph.threads.com", OLD, "GET", "/refresh_access_token",
                                       {"params": {"grant_type": "th_refresh_token"}}))
        self.assertFalse(list(self.directory.glob(".threads-refresh-*")))

    def test_age_expiry_permission_and_dry_run_gates(self):
        cases = [("not_due", {"expires_at": NOW + 8 * refresh.DAY}),
                 ("too_young", {"issued_at": NOW - refresh.DAY + 1}),
                 ("expired", {"expires_at": NOW}), ("invalid", {"is_valid": False}),
                 ("basic_permission_unconfirmed", {"scopes": []}),
                 ("expiry_unknown", {"expires_at": 0})]
        original = self.path.read_bytes()
        base = dict(self.metadata)
        with mock.patch.object(p.APIClient, "request", lambda client, *a, **kw: self.fake(client, *a, **kw)):
            for expected, changes in cases:
                with self.subTest(reason=expected):
                    self.metadata = {**base, **changes}
                    _, output = self.invoke()
                    self.assertEqual(output["status"], expected)
            self.metadata = {**base, "issued_at": NOW - refresh.DAY, "expires_at": NOW + 7 * refresh.DAY}
            code, output = self.invoke(execute=False)
        self.assertEqual(code, 0)
        self.assertEqual(output["status"], "eligible")
        self.assertTrue(all(call[3] == "/debug_token" for call in self.calls))
        self.assertEqual(original, self.path.read_bytes())
        eligible = {**base, "data_access_expires_at": None}
        self.assertEqual(refresh.eligibility(eligible, {"threads_refresh": {"last_refreshed_at": NOW - 60}}, NOW), "too_young")

    def test_mismatch_preserves_original_and_blocks_automatic_retry(self):
        original = self.path.read_bytes()
        with mock.patch.object(p.APIClient, "request", lambda client, *a, **kw: self.fake(client, *a, **kw)), \
                mock.patch.object(runtime, "verify_identity", side_effect=RuntimeError("https://invalid.test/" + NEW)):
            code, output = self.invoke()
            self.assertEqual(code, 2)
            self.assertIn("error", output)
            code, output = self.invoke()
        self.assertEqual(output["reason"], "previous_refresh_outcome_unresolved")
        self.assertEqual(original, self.path.read_bytes())
        self.assertEqual(sum(call[3] == "/refresh_access_token" for call in self.calls), 1)

    def test_concurrent_lock_and_symlink_fail_before_api(self):
        with refresh.locked_config(str(self.path)), mock.patch.object(p.APIClient, "request") as request:
            code, _ = self.invoke()
            self.assertNotEqual(code, 0)
            request.assert_not_called()
        lock = self.directory / ".threads.json.refresh.lock"
        lock.unlink()
        lock.symlink_to(self.path)
        with mock.patch.object(p.APIClient, "request") as request:
            code, _ = self.invoke()
            self.assertNotEqual(code, 0)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
