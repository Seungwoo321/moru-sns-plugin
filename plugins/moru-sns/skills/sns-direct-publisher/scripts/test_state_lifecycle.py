"""Offline regression: initialization is explicit; lost publication history fails closed."""
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
import runtime_guard as guard


class StateLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.config = self.root / "threads.json"
        self.text = self.root / "post.txt"
        self.text.write_text("A development note", encoding="utf-8")
        self.settings = {"version": 1, "platform": "threads", "access_token": "synthetic-state-token",
                         "expected_username": "example_agent", "expected_user_id": "123",
                         "instagram_login_mode": None, "expiry_status": "unknown", "expires_at": None}
        runtime.store_config(self.config, self.settings)
        self.binding = {"threads": {"account_id": "123", "username": "example_agent", "auth_mode": None}}

    def initialize(self):
        guard.initialize_state(self.state, "test-runtime", self.binding)

    def cli(self, command="publish", job="new-job", state=None):
        args = [command, "--platform", "threads", "--state-dir", str(state or self.state), "--job-id", job]
        if command == "publish":
            args += ["--text-file", str(self.text), "--execute"]
        with mock.patch.dict(os.environ, {"THREADS_USER_ACCESS_TOKEN": "synthetic-state-token",
                                          "THREADS_EXPECTED_USER_ID": "123"}), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = p.main(args)
        return code, json.loads(out.getvalue())

    def test_threads_only_initialization_and_recheck_do_not_require_video(self):
        args = ["--threads-config", str(self.config), "--state-dir", str(self.state), "--runtime-id", "test-runtime"]
        with mock.patch.object(runtime, "verify_identity", return_value="123"), \
                mock.patch.object(p.APIClient, "request", return_value={"data": []}) as api, \
                mock.patch.object(guard, "verify_ffprobe", side_effect=AssertionError("not needed")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(guard.main(args + ["--initialize"]), 0)
            self.assertEqual(guard.main(args), 0)
        results = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([item["initialized"] for item in results], [True, False])
        self.assertFalse(results[0]["ffprobe_verified"])
        self.assertEqual(set(results[0]["accounts"]), {"threads"})
        self.assertEqual(api.call_count, 2)
        self.assertEqual(p.inspect_state(self.state), 0)

    def test_x_only_initialization_checks_identity_without_claiming_post_list(self):
        config = self.root / "x.json"
        runtime.store_config(config, {**self.settings, "platform": "x"})
        args = ["--x-config", str(config), "--state-dir", str(self.state),
                "--runtime-id", "test-runtime", "--initialize"]
        with mock.patch.object(runtime, "verify_identity", return_value="123") as identity, \
                mock.patch.object(p.APIClient, "request", side_effect=AssertionError("no post list")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(guard.main(args), 0)
        identity.assert_called_once()
        account = json.loads(out.getvalue())["accounts"]["x"]
        self.assertFalse(account["owned_post_list_checked"])
        self.assertIsNone(account["recent_owned_posts"])

    def test_missing_state_cannot_be_created_by_publish_metrics_or_new_job(self):
        with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("no network")) as api:
            for command in ("publish", "metrics"):
                for job in ("old-job", "new-job"):
                    self.assertNotEqual(self.cli(command, job)[0], 0)
                    self.assertFalse(self.state.exists())
            api.assert_not_called()

    def test_deleted_database_and_deleted_marker_are_not_rebuilt(self):
        for missing in ("publisher.sqlite3", "runtime.json"):
            with self.subTest(missing=missing):
                self.state = self.root / missing.replace(".", "-")
                self.initialize()
                (self.state / missing).unlink()
                before = sorted(p.name for p in self.state.iterdir())
                with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("no network")):
                    self.assertNotEqual(self.cli(job="fresh-job")[0], 0)
                self.assertEqual(before, sorted(p.name for p in self.state.iterdir()))
                self.assertFalse((self.state / missing).exists())

    def test_wrong_account_replaced_ledger_and_open_permissions_block_network(self):
        self.initialize()
        marker = self.state / "runtime.json"
        original = marker.read_text()
        data = json.loads(original)
        data["accounts"]["threads"]["account_id"] = "999"
        marker.write_text(json.dumps(data))
        with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("no network")):
            self.assertNotEqual(self.cli()[0], 0)
            marker.write_text(original)
            self.state.chmod(0o755)
            self.assertNotEqual(self.cli()[0], 0)
            self.state.chmod(0o700)
            database = self.state / "publisher.sqlite3"
            replacement = self.state / "replacement"
            replacement.write_bytes(database.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(database)
            self.assertNotEqual(self.cli()[0], 0)

    def test_git_skill_plugin_and_symlink_paths_are_rejected_without_writes(self):
        for boundary in (".git", "plugin.json", ".codex-plugin/plugin.json"):
            parent = self.root / boundary.replace("/", "-").replace(".", "")
            parent.mkdir(mode=0o700)
            marker = parent / boundary
            marker.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker.write_text("placeholder")
            with self.assertRaises(p.SafeError):
                p.state_path(parent / "state")
            with self.assertRaises(p.SafeError):
                runtime.private_path(parent / "config.json")
            self.assertFalse((parent / "state").exists())
        with self.assertRaises(p.SafeError):
            p.state_path(p.SKILLS_ROOT / "state")
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(p.SafeError):
            p.state_path(alias / "state")

    def test_private_wrapper_stops_before_subprocess_when_ledger_is_missing(self):
        args = ["run", "publish", "--config", str(self.config), "--state-dir", str(self.state),
                "--job-id", "new-job", "--text-file", str(self.text), "--execute"]
        with mock.patch.object(runtime.subprocess, "run", side_effect=AssertionError("no child")) as child, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.main(args), 2)
        child.assert_not_called()
        self.assertFalse(self.state.exists())

    def test_completed_and_uncertain_jobs_survive_reopen_without_repeat_post(self):
        self.initialize()
        ledger = p.Ledger(self.state)
        digest = p.content_hash("A development note", None)
        key = ("threads", "123", "old-job")
        ledger.claim(key, digest)
        ledger.update(key, "published", post_id="789", permalink="https://www.threads.com/@example_agent/post/example")
        ledger.close()
        with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("no duplicate network")) as api:
            code, result = self.cli(job="old-job")
            self.assertEqual((code, result["state"]), (0, "published"))
            self.assertEqual(self.cli(job="new-job")[1]["state"], "duplicate")
            api.assert_not_called()
        ledger = p.Ledger(self.state)
        ledger.update(key, "unknown")
        ledger.close()
        with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("no retry")) as api:
            self.assertEqual(self.cli(job="old-job")[1]["state"], "unknown")
            self.assertEqual(self.cli(job="another-job")[1]["state"], "duplicate")
            api.assert_not_called()

    def test_initialized_cli_can_publish_once_through_fake_api(self):
        self.initialize()
        responses = [{"id": "123", "username": "example_agent"}, {"id": "456"},
                     {"status": "FINISHED"}, {"id": "789"},
                     {"id": "789", "permalink": "https://www.threads.com/@example_agent/post/example"}]
        original = p.Publisher
        def fast_publisher(*args, **kwargs):
            return original(*args, **kwargs, wait=lambda _: None)
        with mock.patch.object(p.APIClient, "request", side_effect=responses) as api, \
                mock.patch.object(p, "Publisher", side_effect=fast_publisher):
            code, result = self.cli(job="publish-once")
            self.assertEqual((code, result["state"], result["post_id"]), (0, "published", "789"))
            calls = api.call_count
            self.assertEqual(self.cli(job="publish-once")[0], 0)
            self.assertEqual(self.cli(job="another-id")[1]["state"], "duplicate")
            self.assertEqual(api.call_count, calls)


if __name__ == "__main__":
    unittest.main()
