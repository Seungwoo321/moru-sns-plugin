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
import token_status as status


CURRENT = "synthetic-current-token+/="
PREVIOUS = "synthetic-previous-token+/="


class TokenStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        os.chmod(self.directory, 0o700)
        self.current = self.save("current.json", CURRENT)
        self.previous = self.save("previous.json", PREVIOUS)

    def save(self, filename, token):
        path = self.directory / filename
        runtime.store_config(path, {"version": 1, "platform": "threads", "access_token": token,
            "expected_username": "example_agent", "expected_user_id": "123", "instagram_login_mode": None,
            "expiry_status": "unknown", "expires_at": None})
        return str(path)

    def invoke(self, *extra):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            code = status.main(["--config", self.current, *extra])
        output = stream.getvalue()
        for token in (CURRENT, PREVIOUS):
            for variant in runtime.secret_variants(token):
                self.assertNotIn(variant, output)
        self.assertNotIn("https://", output)
        return code, json.loads(output)

    def test_current_allowlist_and_missing_fields(self):
        with mock.patch.object(p.APIClient, "request", return_value={"data": {
                "is_valid": True, "user_id": "123", "expires_at": 1800000000,
                "scopes": ["threads_basic", "threads_content_publish"],
                "access_token": CURRENT, "url": "https://invalid.test/?access_token=" + CURRENT,
                "application": "Ignored application name"}}) as request:
            code, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(set(output["current"]), set(status.FIELDS))
        self.assertIsNone(output["current"]["app_id"])
        self.assertIsNone(output["current"]["data_access_expires_at"])
        request.assert_called_once_with("GET", "/debug_token", params={"input_token": CURRENT})

    def test_previous_uses_current_auth_and_changes_no_files(self):
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                  for path in self.directory.iterdir()}
        calls = []
        def fake(client, method, path, **kwargs):
            calls.append((client.base, client.token, method, path, kwargs))
            return {"data": {"is_valid": kwargs["params"]["input_token"] == CURRENT,
                             "user_id": "123", "app_id": "456"}}
        with mock.patch.object(p.APIClient, "request", fake):
            code, output = self.invoke("--previous-config", self.previous)
        self.assertEqual(code, 0)
        self.assertTrue(output["current"]["is_valid"])
        self.assertFalse(output["previous"]["is_valid"])
        self.assertEqual([call[1] for call in calls], [CURRENT, CURRENT])
        self.assertEqual([call[4]["params"]["input_token"] for call in calls], [CURRENT, PREVIOUS])
        self.assertTrue(all(call[0] == "https://graph.threads.com/v1.0" and
                            call[2:4] == ("GET", "/debug_token") for call in calls))
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                 for path in self.directory.iterdir()}
        self.assertEqual(before, after)

    def test_errors_never_become_invalid_or_expose_details(self):
        for result in ({"data": {}}, {"data": {"is_valid": True, "user_id": "999"}},
                       {"data": {"is_valid": True, "scopes": [CURRENT]}}):
            with self.subTest(result_shape=set(result["data"])), mock.patch.object(p.APIClient, "request", return_value=result):
                code, output = self.invoke()
                self.assertNotEqual(code, 0)
                self.assertEqual(set(output), {"error"})
        for error in (p.ApiError(400), RuntimeError("https://invalid.test/?access_token=" + CURRENT)):
            with mock.patch.object(p.APIClient, "request", side_effect=error):
                code, output = self.invoke()
                self.assertNotEqual(code, 0)
                self.assertEqual(set(output), {"error"})
        with mock.patch.object(runtime, "load_config", return_value={"platform": "instagram"}), \
                mock.patch.object(p.APIClient, "request") as request:
            code, _ = self.invoke()
            self.assertNotEqual(code, 0)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
