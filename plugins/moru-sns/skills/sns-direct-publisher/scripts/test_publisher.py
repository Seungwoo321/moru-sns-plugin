"""Offline regression tests. Every API interaction uses a fake client/opener."""
import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest import mock

import publisher as p


class FakeClient:
    def __init__(self, *responses):
        self.responses, self.calls = iter(responses), []

    def request(self, method, path, payload=None, params=None, multipart=None):
        self.calls.append((method, path, payload, params, multipart))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response

    def upload_instagram_video(self, container_id, uri, video):
        self.calls.append(("UPLOAD", uri, container_id, video.sha256, video.size))
        response = next(self.responses)
        if isinstance(response, BaseException):
            raise response
        return response


X_ME = {"data": {"id": "123", "username": "test_bot"}}
TH_ME = {"id": "123", "username": "test_bot"}
IG_ME = {"user_id": "123", "id": "999", "username": "test_bot"}
X_INIT = {"data": {"id": "700", "expires_after_secs": 86400}}


def probe_output(duration="30.0"):
    return {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": duration},
            "streams": [{"codec_type": "video", "codec_name": "h264", "width": 640,
                         "pix_fmt": "yuv420p", "avg_frame_rate": "30/1", "field_order": "progressive"}]}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        # Protocol/state-machine fixtures use fake MP4 bytes; real delivery policy
        # is exercised without this mock in test_video_preflight.py.
        gate=mock.patch('video_preflight.require_new',return_value=None);gate.start();self.addCleanup(gate.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state"
        import runtime_guard as guard
        guard.initialize_state(self.state, "test-runtime", {
            platform: {"account_id": "123", "username": "test_bot",
                       "auth_mode": "instagram" if platform == "instagram" else None}
            for platform in p.BASES})
        self.ledger = p.Ledger(self.state)
        self.now = 1000

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def make(self, platform, *responses):
        client = FakeClient(*responses)
        return p.Publisher(platform, "123", client, self.ledger, wait=lambda _: None, clock=lambda: self.now,
                           instagram_login_mode="instagram" if platform == "instagram" else None), client

    def video(self, platform="x", body=b"offline video fixture", ai=False):
        path = Path(self.temp.name) / (p.hashlib.sha256(body).hexdigest() + ".mp4")
        path.write_bytes(body)
        with mock.patch.object(p.shutil, "which", return_value="/test/ffprobe"), \
                mock.patch.object(p.subprocess, "run", return_value=mock.Mock(stdout=json.dumps(probe_output()).encode())):
            return p.prepare_video(platform, path, "https://media.example.com/immutable.mp4" if platform in ("threads", "instagram") else None, ai)

    def root_receipt(self, job="root", account="123", state="published"):
        key = ("threads", account, job)
        self.ledger.claim(key, p.content_hash("Root " + job + account))
        self.ledger.update(key, state, post_id="800" if state == "published" else None)

    def test_followup_requires_saved_published_own_root_before_any_request(self):
        self.root_receipt("pending", state="media_processing")
        self.root_receipt("other", account="456")
        self.root_receipt("reply")
        self.ledger.update(("threads", "123", "reply"), "published", reply_to_id="700")
        for parent in ("missing", "pending", "other", "reply"):
            pub, client = self.make("threads")
            with self.subTest(parent=parent), self.assertRaises(p.SafeError):
                pub.publish("follow-" + parent, "More detail", reply_to_job_id=parent)
            self.assertEqual(client.calls, [])

    def test_followup_rechecks_owner_and_never_replies_to_other_account(self):
        self.root_receipt()
        pub, client = self.make("threads", TH_ME, {"id": "800", "owner": {"id": "456"}})
        with self.assertRaises(p.SafeError):
            pub.publish("follow", "More detail", reply_to_job_id="root")
        self.assertTrue(all(c[0] == "GET" for c in client.calls))

    def test_followup_binds_parent_and_preserves_duplicate_uncertain_guards(self):
        self.root_receipt()
        self.root_receipt("second")
        self.ledger.update(("threads", "123", "second"), "published", post_id="801")
        parent = {"id": "800", "owner": {"id": "123"}}
        pub, client = self.make("threads", TH_ME, parent, {"id": "700"}, {"status": "FINISHED"}, p.ApiError())
        self.assertEqual(pub.publish("follow", "More detail", reply_to_job_id="root")["state"], "unknown")
        self.assertEqual(client.calls[2][2]["reply_to_id"], "800")
        self.assertEqual(client.calls[2][2]["is_made_with_ai"], "true")
        count = len(client.calls)
        pub.publish("follow", "More detail", reply_to_job_id="root")
        self.assertEqual(pub.publish("alias", "More detail", reply_to_job_id="root")["state"], "duplicate")
        with self.assertRaises(p.SafeError):
            pub.publish("follow", "More detail", reply_to_job_id="second")
        self.assertEqual(len(client.calls), count)

    def test_followup_offline_plan_reads_receipt_without_mutating_it(self):
        self.root_receipt()
        db = self.state / "publisher.sqlite3"
        before = db.read_bytes()
        self.assertEqual(p.read_reply_parent(self.state, "123", "root"), "800")
        self.assertEqual(db.read_bytes(), before)

    def test_instagram_checks_professional_user_id_not_app_scoped_id(self):
        for response in (IG_ME, {"data": [IG_ME]}):
            pub, client = self.make("instagram", response)
            self.assertTrue(pub.check()["account_matches"])
            self.assertEqual(client.calls[0][3], {"fields": "user_id,username"})
        pub, client = self.make("instagram", {"user_id": "456", "id": "123"})
        with self.assertRaises(p.SafeError):
            pub.publish("wrong", "A demo", self.video("instagram"))
        self.assertEqual([c[0] for c in client.calls], ["GET"])

    def test_instagram_reels_processing_publish_ai_flag_and_duplicates(self):
        video = self.video("instagram", ai=False)
        pub, client = self.make("instagram", IG_ME, {"id": "700"}, IG_ME, {"status_code": "IN_PROGRESS"},
                                IG_ME, {"status_code": "FINISHED"}, {"id": "900"},
                                {"id": "900", "permalink": "https://www.instagram.com/reel/abc/"})
        self.assertEqual(pub.publish("reel", "A demo", video)["state"], "media_processing")
        self.assertEqual(client.calls[1][2], {"media_type": "REELS", "caption": "A demo",
                                            "video_url": video.url, "is_ai_generated": "false"})
        self.now += 60
        self.assertEqual(pub.publish("reel", "A demo", video)["state"], "media_processing")
        self.now += 60
        self.assertEqual(pub.publish("reel", "A demo", video)["state"], "published")
        self.assertEqual(client.calls[3][3], {"fields": "status_code"})
        self.assertEqual([c[1] for c in client.calls if c[0] == "POST"], ["/123/media", "/123/media_publish"])
        count = len(client.calls)
        pub.publish("reel", "A demo", video)
        self.assertEqual(pub.publish("alias", "A demo", video)["state"], "duplicate")
        self.assertEqual(len(client.calls), count)

    def test_instagram_rejects_non_reel_and_locks_unknown_publish(self):
        pub, client = self.make("instagram")
        with self.assertRaises(p.SafeError):
            pub.publish("plain", "A demo")
        self.assertEqual(client.calls, [])
        video = self.video("instagram")
        pub, client = self.make("instagram", IG_ME, {"id": "700"}, IG_ME,
                                {"status_code": "FINISHED"}, p.ApiError())
        pub.publish("reel", "A demo", video)
        self.now += 60
        self.assertEqual(pub.publish("reel", "A demo", video)["state"], "unknown")
        count = len(client.calls)
        pub.publish("reel", "A demo", video)
        self.assertEqual(len(client.calls), count)

    def test_instagram_metrics_preserve_missing_values_and_verify_owner(self):
        key = ("instagram", "123", "reel")
        self.ledger.claim(key, "fixture")
        self.ledger.update(key, "published", post_id="900")
        pub, client = self.make("instagram", IG_ME, {"owner": {"id": "123"}},
                                {"data": [{"name": "views", "values": []},
                                          {"name": "likes", "values": [{"value": 0}]},
                                          {"name": "reach", "total_value": {"value": 2}}]})
        self.assertEqual(pub.metrics("reel")["metrics"], {"views": None, "likes": 0, "reach": 2})
        self.assertEqual(client.calls[-1][3], {"metric": "views,reach,likes,comments,saved,shares"})

    def test_media_origin_and_voice_determine_ai_disclosure(self):
        video = self.video("instagram")
        for kind, audio, expected in (("real-demo", "original", False), ("blender-render", "none", False),
                                       ("real-demo", "ai-realistic", True), ("ai-stylized", "none", True)):
            with mock.patch.object(p.shutil, "which", return_value=None):
                result = p.prepare_video("instagram", video.path, video.url, video_kind=kind, audio_kind=audio)
            self.assertEqual(result.ai_generated, expected)
            self.assertEqual(result.summary()["video_kind"], kind)

    def test_instagram_plan_is_offline_and_shows_exact_ai_field(self):
        video = self.video("instagram")
        text_file = Path(self.temp.name) / "caption.txt"
        text_file.write_text("실제 동작 시연", encoding="utf-8")
        unused_state = Path(self.temp.name) / "unused-state"
        output = io.StringIO()
        args = ["plan", "--platform", "instagram", "--instagram-login-mode", "instagram", "--state-dir", str(unused_state), "--job-id", "reel",
                "--text-file", str(text_file), "--video-file", str(video.path), "--video-url", video.url]
        with mock.patch.dict(p.os.environ, {"INSTAGRAM_EXPECTED_USER_ID": "123"}, clear=True), \
                mock.patch.object(p, "prepare_video", return_value=video), \
                mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(p.main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual((result["ai_label"]["field"], result["ai_label"]["value"]), ("is_ai_generated", False))
        self.assertNotIn(video.url, output.getvalue())
        self.assertFalse(unused_state.exists())

    def test_instagram_client_uses_fixed_host_version_and_form_fields(self):
        client = p.APIClient("instagram", "offline-test-token", "instagram")
        response = mock.Mock(status=200)
        response.read.return_value = b'{"id":"700"}'
        client.opener = mock.Mock()
        client.opener.open.return_value.__enter__ = mock.Mock(return_value=response)
        client.opener.open.return_value.__exit__ = mock.Mock(return_value=False)
        client.request("POST", "/123/media", {"media_type": "REELS", "is_ai_generated": "true"})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://graph.instagram.com/v27.0/123/media")
        body = p.urllib.parse.parse_qs(request.data.decode())
        self.assertEqual(body["is_ai_generated"], ["true"])
        self.assertEqual(body["access_token"], ["offline-test-token"])

    def unified(self, *responses, upload_mode=None):
        client = FakeClient(*responses)
        return p.Publisher("instagram", "123", client, self.ledger, wait=lambda _: None,
                           clock=lambda: self.now, instagram_login_mode="unified",
                           instagram_upload_mode=upload_mode), client

    def test_unified_direct_upload_persists_before_streaming_and_resumes_without_reupload(self):
        assigned = {"data": [{"id": "123"}]}
        uri = p.INSTAGRAM_UPLOAD_BASE + "/700"
        video = replace(self.video("instagram"), url=None)
        pub, client = self.unified(assigned, {"id": "700", "uri": uri}, {"success": True},
                                   upload_mode="resumable")
        observed, original_upload = [], client.upload_instagram_video

        def observe_upload(*args):
            observed.append(self.ledger.get(("instagram", "123", "direct")))
            return original_upload(*args)

        with mock.patch.object(client, "upload_instagram_video", side_effect=observe_upload):
            pending = pub.publish("direct", "A local demo", video)
        self.assertEqual((observed[0]["state"], observed[0]["container_id"], observed[0]["upload_mode"]),
                         ("uploading", "700", "resumable"))
        self.assertEqual(pending["state"], "media_processing")
        self.assertEqual(pending["media_expires_at"], self.now + 86400)
        self.assertEqual(client.calls[1][2], {"media_type": "REELS", "caption": "A local demo",
                                             "is_ai_generated": "false", "upload_type": "resumable"})
        self.assertEqual(client.calls[2], ("UPLOAD", uri, "700", video.sha256, video.size))
        self.ledger.close()
        self.ledger = p.Ledger(self.state)
        self.now += 60
        resumed, resumed_client = self.unified(assigned, {"status_code": "FINISHED"}, {"id": "900"},
                                              {"id": "900", "permalink": "https://www.instagram.com/reel/demo/"},
                                              upload_mode="resumable")
        result = resumed.publish("direct", "A local demo", video)
        self.assertEqual((result["state"], result["post_id"]), ("published", "900"))
        self.assertEqual([c[1] for c in resumed_client.calls if c[0] == "POST"], ["/123/media_publish"])
        count = len(resumed_client.calls)
        resumed.publish("direct", "A local demo", video)
        self.assertEqual(resumed.publish("alias", "A local demo", video)["state"], "duplicate")
        self.assertEqual(len(resumed_client.calls), count)

    def test_direct_upload_rejects_foreign_or_mismatched_uri_before_sending_bytes(self):
        expected = p.INSTAGRAM_UPLOAD_BASE + "/700"
        invalid = [None, expected.replace("https:", "http:"), expected + "?token=anything", expected + "#fragment",
                   expected.replace("rupload.facebook.com", "rupload.facebook.com.evil.example"),
                   expected.replace("rupload.facebook.com", "user@rupload.facebook.com"),
                   expected.replace("/v27.0/", "/v26.0/"), expected.replace("/700", "/701"), expected + "/"]
        video = replace(self.video("instagram"), url=None)
        for index, uri in enumerate(invalid):
            with self.subTest(uri=uri):
                pub, client = self.unified({"data": [{"id": "123"}]}, {"id": "700", "uri": uri},
                                           upload_mode="resumable")
                with self.assertRaises(p.SafeError):
                    pub.publish("bad-uri-" + str(index), "Demo " + str(index), video)
                self.assertFalse(any(c[0] == "UPLOAD" for c in client.calls))
                self.assertEqual(self.ledger.get(("instagram", "123", "bad-uri-" + str(index)))["state"], "failed")

    def test_direct_upload_unknown_result_and_process_crash_cannot_resend(self):
        video = replace(self.video("instagram"), url=None)
        for index, error in enumerate((p.ApiError(), p.ApiError(503), KeyboardInterrupt())):
            job, text = "upload-stop-" + str(index), "Demo stop " + str(index)
            pub, client = self.unified({"data": [{"id": "123"}]},
                                       {"id": "700", "uri": p.INSTAGRAM_UPLOAD_BASE + "/700"}, error,
                                       upload_mode="resumable")
            if isinstance(error, KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    pub.publish(job, text, video)
                self.assertEqual(self.ledger.get(("instagram", "123", job))["state"], "uploading")
            else:
                self.assertEqual(pub.publish(job, text, video)["state"], "unknown")
            count = len(client.calls)
            pub.publish(job, text, video)
            self.assertEqual(pub.publish(job + "-alias", text, video)["state"], "duplicate")
            self.assertEqual(len(client.calls), count)

    def test_direct_upload_mode_and_identity_cannot_be_inferred_or_changed(self):
        video = self.video("instagram")
        for platform, login in (("threads", None), ("x", None), ("instagram", "instagram")):
            with self.assertRaises(p.SafeError):
                p.Publisher(platform, "123", FakeClient(), self.ledger,
                            instagram_login_mode=login, instagram_upload_mode="resumable")
        pub, client = self.unified(upload_mode="resumable")
        with self.assertRaises(p.SafeError):
            pub.publish("url-forbidden", "A demo", video)
        self.assertEqual(client.calls, [])
        pub, client = self.unified({"data": [{"id": "456"}]}, upload_mode="resumable")
        with self.assertRaises(p.SafeError):
            pub.publish("wrong-direct-owner", "A demo", replace(video, url=None))
        self.assertTrue(all(c[0] == "GET" for c in client.calls))
        direct, _ = self.unified({"data": [{"id": "123"}]},
                                 {"id": "700", "uri": p.INSTAGRAM_UPLOAD_BASE + "/700"}, {"success": True},
                                 upload_mode="resumable")
        direct.publish("transport-bound", "Bound demo", replace(video, url=None))
        standard, standard_client = self.unified()
        with self.assertRaises(p.SafeError):
            standard.publish("transport-bound", "Bound demo", video)
        self.assertEqual(standard.publish("transport-alias", "Bound demo", video)["state"], "duplicate")
        self.assertEqual(standard_client.calls, [])

    def test_direct_upload_preserves_expiry_and_five_poll_limit(self):
        assigned = {"data": [{"id": "123"}]}
        video = replace(self.video("instagram"), url=None)
        for expired in (False, True):
            job, text = ("direct-expired", "Expired demo") if expired else ("direct-stalled", "Stalled demo")
            responses = [assigned, {"id": "700", "uri": p.INSTAGRAM_UPLOAD_BASE + "/700"}, {"success": True}]
            responses += [assigned] if expired else [item for _ in range(5) for item in (assigned, {"status_code": "IN_PROGRESS"})] + [assigned]
            pub, client = self.unified(*responses, upload_mode="resumable")
            pub.publish(job, text, video)
            if expired:
                self.now += 86401
            else:
                for _ in range(5):
                    self.now += 60
                    self.assertEqual(pub.publish(job, text, video)["state"], "media_processing")
                self.now += 60
            result = pub.publish(job, text, video)
            self.assertEqual(result["state"], "failed" if expired else "processing_stalled")
            self.assertFalse(any(c[1].endswith("/media_publish") for c in client.calls))
            self.assertEqual(sum(c[0] == "UPLOAD" for c in client.calls), 1)

    def test_direct_client_streams_exact_bytes_headers_and_refuses_redirects(self):
        video = replace(self.video("instagram", body=b"local-binary-fixture"), url=None)
        client = p.APIClient("instagram", "offline-credential", "unified")
        self.assertTrue(any(isinstance(handler, p.NoRedirect) for handler in client.opener.handlers))
        captured = []

        def send(request, timeout):
            raw = b""
            while chunk := request.data.read(7):
                raw += chunk
            captured.append((request, raw))
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"success": true, "message": "ignored remote text"}'
            return response

        client.opener = mock.Mock()
        client.opener.open.side_effect = send
        uri = p.INSTAGRAM_UPLOAD_BASE + "/700"
        self.assertEqual(client.upload_instagram_video("700", uri, video), {"success": True})
        request, raw = captured[0]
        self.assertEqual(raw, b"local-binary-fixture")
        self.assertEqual(request.full_url, uri)
        self.assertNotIn("offline-credential", request.full_url)
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["authorization"], "OAuth offline-credential")
        self.assertEqual((headers["offset"], headers["file_size"], headers["content-length"]),
                         ("0", str(video.size), str(video.size)))
        client.opener.open.side_effect = p.urllib.error.HTTPError(uri, 302, "private remote error", {}, None)
        with self.assertRaises(p.ApiError) as caught:
            client.upload_instagram_video("700", uri, video)
        self.assertEqual(caught.exception.status, 302)
        self.assertEqual(str(caught.exception), "API request failed.")

    def test_direct_client_detects_file_changes_during_stream(self):
        video = replace(self.video("instagram", body=b"original-binary"), url=None)
        client = p.APIClient("instagram", "offline-credential", "unified")
        client.opener = mock.Mock()

        def mutate(request, timeout):
            while request.data.read(4):
                pass
            video.path.write_bytes(b"replaced-binary")
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'{"success": true}'
            return response

        client.opener.open.side_effect = mutate
        with self.assertRaises(p.SafeError):
            client.upload_instagram_video("700", p.INSTAGRAM_UPLOAD_BASE + "/700", video)

    def test_resumable_plan_is_offline_without_public_url_and_mode_is_explicit(self):
        video = replace(self.video("instagram"), url=None)
        text_file = Path(self.temp.name) / "caption.txt"
        text_file.write_text("A local demo", encoding="utf-8")
        unused = Path(self.temp.name) / "unused"
        args = ["plan", "--platform", "instagram", "--instagram-login-mode", "unified",
                "--instagram-upload-mode", "resumable", "--state-dir", str(unused), "--job-id", "direct-plan",
                "--text-file", str(text_file), "--video-file", str(video.path)]
        output = io.StringIO()
        with mock.patch.dict(p.os.environ, {}, clear=True), mock.patch.object(p, "prepare_video", return_value=video), \
                mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(p.main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["upload_mode"], "resumable")
        self.assertNotIn("video_url_missing", result["readiness"]["reasons"])
        self.assertFalse(result["publish_ready"])
        self.assertFalse(unused.exists())
        for changes in ({"--platform": "threads"}, {"--instagram-login-mode": "instagram"}, {"--video-url": "https://media.example.com/demo.mp4"}):
            invalid = list(args)
            for flag, value in changes.items():
                if flag in invalid:
                    invalid[invalid.index(flag) + 1] = value
                else:
                    invalid += [flag, value]
            with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(p.main(invalid), 2)

    def test_private_wrapper_forwards_upload_mode_without_credential_in_arguments(self):
        import private_runtime as runtime
        args = ["run", "plan", "--config", "/not-read/credentials.json", "--state-dir", str(self.state),
                "--instagram-upload-mode", "resumable", "--job-id", "wrapper", "--text-file", "/not-read/caption.txt",
                "--video-file", "/not-read/demo.mp4"]
        fixture = {"platform": "instagram", "instagram_login_mode": "unified", "expected_user_id": "123",
                   "access_token": "offline-wrapper-credential"}
        with mock.patch.object(runtime, "load_config", return_value=fixture), \
                mock.patch.object(runtime.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="{}", stderr="")) as run, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(runtime.main(args), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--instagram-upload-mode") + 1], "resumable")
        self.assertNotIn("offline-wrapper-credential", " ".join(command))
        self.assertEqual(run.call_args.kwargs["env"]["INSTAGRAM_USER_ACCESS_TOKEN"], "offline-wrapper-credential")

    def test_unified_selects_exact_assigned_account_and_fixed_cursor_endpoint(self):
        pub, client = self.unified({"data": [{"id": "456"}], "paging": {
            "cursors": {"after": "cursor_1"}, "next": "https://untrusted.example.com/ignore"}},
            {"data": [{"id": "789"}, {"id": "123"}]})
        self.assertEqual(pub.check()["auth_mode"], "unified")
        self.assertEqual([c[1] for c in client.calls], ["/me/assigned_instagram_accounts"] * 2)
        self.assertEqual(client.calls[1][3], {"fields": "id,username", "after": "cursor_1"})

    def test_unified_missing_account_or_bad_cursor_never_creates_media(self):
        for result in ({"data": [{"id": "456"}]},
                       {"data": [], "paging": {"cursors": {"after": "https://evil.example.com"}}},
                       {"data": [], "paging": {"next": "https://evil.example.com"}}):
            pub, client = self.unified(result)
            with self.assertRaises(p.SafeError):
                pub.publish("blocked-" + str(len(json.dumps(result))), "A demo", self.video("instagram"))
            self.assertTrue(all(c[0] == "GET" for c in client.calls))

    def test_unified_repeated_cursor_stops_and_cannot_guess_account(self):
        page = {"data": [{"id": "456"}], "paging": {"cursors": {"after": "same"}}}
        pub, client = self.unified(page, page)
        with self.assertRaises(p.SafeError):
            pub.check()
        self.assertEqual(len(client.calls), 2)

    def test_unified_reel_uses_same_safe_publish_lifecycle(self):
        assigned = {"data": [{"id": "456"}, {"id": "123"}]}
        pub, client = self.unified(assigned, {"id": "700"}, assigned, {"status_code": "FINISHED"},
                                   {"id": "900"}, {"id": "900", "permalink": "https://www.instagram.com/reel/abc/"})
        video = self.video("instagram")
        self.assertEqual(pub.publish("unified-reel", "A demo", video)["state"], "media_processing")
        self.now += 60
        self.assertEqual(pub.publish("unified-reel", "A demo", video)["state"], "published")
        self.assertEqual(client.calls[1][1:3], ("/123/media", {"media_type": "REELS", "caption": "A demo",
                                                               "is_ai_generated": "false", "video_url": video.url}))
        self.assertEqual([c[1] for c in client.calls if c[0] == "POST"], ["/123/media", "/123/media_publish"])

    def test_pending_reel_binds_login_mode_and_mode_change_cannot_bypass_duplicates(self):
        pub, _ = self.unified({"data": [{"id": "123"}]}, {"id": "700"})
        video = self.video("instagram")
        pending = pub.publish("mode-bound", "A demo", video)
        self.assertEqual(pending["auth_mode"], "unified")
        self.now += 60
        wrong_mode, client = self.make("instagram")
        with self.assertRaises(p.SafeError):
            wrong_mode.publish("mode-bound", "A demo", video)
        self.assertEqual(wrong_mode.publish("new-alias", "A demo", video)["state"], "duplicate")
        self.assertEqual(client.calls, [])

    def test_instagram_host_and_mode_are_explicit(self):
        with self.assertRaises(p.SafeError):
            p.APIClient("instagram", "offline-token")
        self.assertEqual(p.APIClient("instagram", "offline-token", "unified").base, "https://graph.facebook.com/v27.0")
        self.assertEqual(p.APIClient("instagram", "offline-token", "instagram").base, "https://graph.instagram.com/v27.0")
        output = io.StringIO()
        with mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), contextlib.redirect_stdout(output):
            self.assertEqual(p.main(["check", "--platform", "instagram", "--state-dir", str(self.state)]), 2)

    def test_published_job_and_same_content_cannot_repost(self):
        pub, client = self.make("x", X_ME, {"data": {"id": "900"}})
        self.assertEqual(pub.publish("one", "개발 기록")["state"], "published")
        self.assertEqual(pub.publish("one", "개발 기록")["post_id"], "900")
        self.assertEqual(pub.publish("two", "개발 기록")["state"], "duplicate")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1][2], {"text": "개발 기록"})

    def test_same_job_binds_text_even_for_duplicate_alias(self):
        pub, client = self.make("x", X_ME, {"data": {"id": "900"}})
        pub.publish("one", "Original")
        pub.publish("two", "Original")
        for job in ("one", "two"):
            with self.assertRaises(p.SafeError):
                pub.publish(job, "Changed")
        self.assertEqual(len(client.calls), 2)

    def test_wrong_identity_never_posts(self):
        pub, client = self.make("x", {"data": {"id": "456"}})
        with self.assertRaises(p.SafeError):
            pub.publish("one", "Original")
        self.assertEqual(pub.publish("one", "Original")["state"], "failed")
        self.assertEqual([c[0] for c in client.calls], ["GET"])

    def test_http_failure_retry_rules(self):
        for status, state in ((400, "failed"), (408, "unknown"), (409, "unknown"), (429, "failed"), (500, "unknown"), (None, "unknown")):
            with self.subTest(status=status):
                job, text = str(status), "Text " + str(status)
                pub, client = self.make("x", X_ME, p.ApiError(status))
                self.assertEqual(pub.publish(job, text)["state"], state)
                pub.publish(job, text)
                self.assertEqual(len(client.calls), 2)
                next_pub, next_client = self.make("x", X_ME, {"data": {"id": "901"}})
                next_result = next_pub.publish(job + "new", text)
                self.assertEqual(next_result["state"], "published" if state == "failed" else "duplicate")
                self.assertEqual(len(next_client.calls), 2 if state == "failed" else 0)

    def test_real_client_timeout_is_generic_and_blocks_future_posts(self):
        client = p.APIClient("x", "secret-token-must-not-appear")
        client.opener = mock.Mock()
        client.opener.open.side_effect = TimeoutError("secret-token-must-not-appear")
        pub = p.Publisher("x", "123", client, self.ledger)
        result = pub.publish("one", "Original")
        self.assertEqual(result["state"], "unknown")
        self.assertNotIn("secret-token", json.dumps(result))
        pub.publish("one", "Original")
        self.assertEqual(client.opener.open.call_count, 1)

    def test_threads_two_steps_persist_container_before_publish(self):
        pub, client = self.make("threads", TH_ME, {"id": "700"}, {"status": "FINISHED"},
                                {"id": "900"}, {"id": "900", "permalink": "https://www.threads.com/@bot/post/abc"})
        waits = []
        pub.wait = waits.append
        result = pub.publish("one", "Original")
        self.assertEqual(result["state"], "published")
        self.assertEqual((result["container_id"], result["post_id"]), ("700", "900"))
        self.assertEqual(waits, [30])
        self.assertEqual(client.calls[3][2], {"creation_id": "700"})
        self.assertEqual(client.calls[1][2]["is_made_with_ai"], "true")

    def test_threads_pending_is_resumable_but_incomplete_post_receipt_is_locked(self):
        for number, responses in enumerate((({"status": "IN_PROGRESS"},),
                                            ({"status": "FINISHED"}, {}))):
            pub, client = self.make("threads", TH_ME, {"id": "700"}, *responses)
            text, job = "Original " + str(number), str(number)
            result = pub.publish(job, text)
            self.assertEqual((result["state"], result["container_id"]), ("media_processing" if number == 0 else "unknown", "700"))
            calls = len(client.calls)
            pub.publish(job, text)
            self.assertEqual(len(client.calls), calls)

    def test_optional_permalink_failure_preserves_successful_receipt(self):
        pub, client = self.make("threads", TH_ME, {"id": "700"}, {"status": "FINISHED"},
                                {"id": "900"}, p.ApiError(500))
        result = pub.publish("one", "Original")
        self.assertEqual((result["state"], result["post_id"]), ("published", "900"))
        pub.publish("one", "Original")
        self.assertEqual(len(client.calls), 5)

    def test_existing_claim_after_crash_cannot_publish(self):
        self.ledger.claim(("x", "123", "one"), p.hashlib.sha256(b"Original").hexdigest())
        pub, client = self.make("x")
        self.assertEqual(pub.publish("one", "Original")["state"], "claimed")
        self.assertEqual(client.calls, [])

    def test_malformed_post_response_is_unknown_and_locked(self):
        pub, client = self.make("x", X_ME, {"data": []})
        self.assertEqual(pub.publish("one", "Original")["state"], "unknown")
        pub.publish("one", "Original")
        self.assertEqual(len(client.calls), 2)

    def test_malformed_profile_is_generic_and_never_posts(self):
        pub, client = self.make("x", {"data": []})
        self.assertEqual(pub.publish("one", "Original")["state"], "unknown")
        self.assertEqual([c[0] for c in client.calls], ["GET"])

    def test_concurrent_claims_allow_only_one_owner(self):
        barrier = threading.Barrier(2)

        def claim(job):
            ledger = p.Ledger(self.state)
            try:
                barrier.wait(timeout=5)
                return ledger.claim(("x", "123", job), "same-hash")[1]
            finally:
                ledger.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(claim, ("first", "second")))
        self.assertEqual(sorted(results), [False, True])

    def test_metrics_only_for_stored_owned_post_and_preserves_zero(self):
        pub, _ = self.make("x", X_ME, {"data": {"id": "900"}})
        pub.publish("one", "Original")
        reader, _ = self.make("x", X_ME, {"data": {"author_id": "123", "public_metrics": {"like_count": 0}}})
        self.assertEqual(reader.metrics("one")["metrics"], {"like_count": 0})
        with self.assertRaises(p.SafeError):
            reader.metrics("unrecorded")
        bad, _ = self.make("x", X_ME, {"data": {"author_id": "456", "public_metrics": {}}})
        with self.assertRaises(p.SafeError):
            bad.metrics("one")

    def test_plan_has_no_network_or_ledger_writes(self):
        text_file, other_state = Path(self.temp.name) / "text.txt", Path(self.temp.name) / "new-state"
        text_file.write_text("개발 기록", encoding="utf-8")
        args = ["plan", "--platform", "x", "--state-dir", str(other_state), "--job-id", "one", "--text-file", str(text_file)]
        output = io.StringIO()
        with mock.patch.dict(p.os.environ, {"X_EXPECTED_USER_ID": "123"}, clear=True), \
                mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(p.main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual((result["preview"], result["network_requests"]), ("개발 기록", 0))
        self.assertFalse(other_state.exists())

    def test_unconfigured_plan_reviews_text_without_network_or_state(self):
        text_file = Path(self.temp.name) / "unconfigured.txt"
        text_file.write_text("개발 기록", encoding="utf-8")
        unused_state = Path(self.temp.name) / "unconfigured-state"
        for reply_options in ([], ["--reply-to-job-id", "saved-root"]):
            args = ["plan", "--platform", "threads", "--state-dir", str(unused_state),
                    "--job-id", "review", "--text-file", str(text_file)] + reply_options
            output = io.StringIO()
            with self.subTest(reply=bool(reply_options)), mock.patch.dict(p.os.environ, {}, clear=True), \
                    mock.patch.object(p, "APIClient", side_effect=AssertionError("No network client")), \
                    mock.patch.object(p, "Ledger", side_effect=AssertionError("No ledger writes")), \
                    mock.patch.object(p.sqlite3, "connect", side_effect=AssertionError("No account means no parent lookup")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(p.main(args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["preview"], "개발 기록")
            self.assertIsNone(result["account_id"])
            self.assertFalse(result["publish_ready"])
            self.assertEqual((result["network_requests"], result["state_writes"]), (0, 0))
            self.assertEqual(result["readiness"]["status"], "NOT_READY")
            self.assertIn("account_unconfigured", result["readiness"]["reasons"])
            if reply_options:
                self.assertIsNone(result["content_sha256"])
                self.assertIsNone(result["reply_to_id"])
                self.assertIn("reply_parent_unverified", result["readiness"]["reasons"])
            self.assertFalse(unused_state.exists())

    def test_local_video_plan_without_public_url_reports_missing_readiness(self):
        text_file, video_file = Path(self.temp.name) / "caption.txt", Path(self.temp.name) / "review.mp4"
        text_file.write_text("화면 시연", encoding="utf-8")
        video_file.write_bytes(b"offline fixture; ffprobe mocked")
        unused_state = Path(self.temp.name) / "local-video-state"
        for platform in ("threads", "instagram"):
            args = ["plan", "--platform", platform, "--state-dir", str(unused_state), "--job-id", "review",
                    "--text-file", str(text_file), "--video-file", str(video_file)]
            if platform == "instagram":
                args += ["--instagram-login-mode", "unified"]
            output = io.StringIO()
            with self.subTest(platform=platform), mock.patch.dict(p.os.environ, {}, clear=True), \
                    mock.patch.object(p.shutil, "which", return_value="/test/ffprobe"), \
                    mock.patch.object(p.subprocess, "run", return_value=mock.Mock(stdout=json.dumps(probe_output()).encode())), \
                    mock.patch.object(p, "APIClient", side_effect=AssertionError("No network client")), \
                    mock.patch.object(p, "Ledger", side_effect=AssertionError("No ledger writes")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(p.main(args), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["media"]["local_preflight"], "verified_local_profile")
            self.assertFalse(result["media"]["public_video_url_provided"])
            self.assertEqual(result["readiness"]["status"], "NOT_READY")
            self.assertIn("video_url_missing", result["readiness"]["reasons"])
            self.assertEqual((result["network_requests"], result["state_writes"]), (0, 0))
            self.assertFalse(unused_state.exists())

    def test_live_commands_still_require_account_token_and_meta_video_url(self):
        text_file = Path(self.temp.name) / "live-guard.txt"
        text_file.write_text("Guarded content", encoding="utf-8")
        unused_state = Path(self.temp.name) / "guarded-state"
        for command in ("check", "publish", "metrics"):
            args = [command, "--platform", "threads", "--state-dir", str(unused_state),
                    "--job-id", "guarded", "--text-file", str(text_file), "--execute"]
            for env in ({}, {"THREADS_EXPECTED_USER_ID": "not-numeric"}, {"THREADS_EXPECTED_USER_ID": "123"}):
                with self.subTest(command=command, configured=bool(env)), mock.patch.dict(p.os.environ, env, clear=True), \
                        mock.patch.object(p, "APIClient", side_effect=AssertionError("No network client")), \
                        mock.patch.object(p, "Ledger", side_effect=AssertionError("No ledger writes")), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(p.main(args), 2)
                self.assertFalse(unused_state.exists())
        for platform in ("threads", "instagram"):
            video = self.video(platform)
            without_url = p.Video(video.path, video.sha256, video.size, video.stamp, video.duration, video.preflight)
            pub, client = self.make(platform)
            with self.subTest(platform=platform), self.assertRaises(p.SafeError):
                pub.publish("no-url", "A real demo", without_url)
            self.assertEqual(client.calls, [])
            self.assertIsNone(self.ledger.get((platform, "123", "no-url")))
            args = ["publish", "--platform", platform, "--state-dir", str(unused_state), "--job-id", "no-url",
                    "--text-file", str(text_file), "--video-file", str(video.path), "--execute"]
            if platform == "instagram":
                args += ["--instagram-login-mode", "instagram"]
            with mock.patch.dict(p.os.environ, {platform.upper() + "_EXPECTED_USER_ID": "123",
                                               platform.upper() + "_USER_ACCESS_TOKEN": "test-token"}, clear=True), \
                    mock.patch.object(p, "APIClient", side_effect=AssertionError("No network client")), \
                    mock.patch.object(p, "Ledger", side_effect=AssertionError("No ledger writes")), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(p.main(args), 2)
            self.assertFalse(unused_state.exists())

    def test_threads_empty_metric_values_stay_unknown_not_zero(self):
        pub, _ = self.make("threads", TH_ME, {"id": "700"}, {"status": "FINISHED"},
                           {"id": "900"}, p.ApiError(500))
        pub.publish("one", "Original")
        reader, _ = self.make("threads", TH_ME, {"owner": {"id": "123"}},
                              {"data": [{"name": "views", "values": []}, {"name": "likes", "values": [{"value": 0}]}]})
        self.assertEqual(reader.metrics("one")["metrics"], {"views": None, "likes": 0})
        for malformed in ([], {"owner": []}):
            bad, _ = self.make("threads", TH_ME, malformed)
            with self.assertRaises(p.ApiError):
                bad.metrics("one")

    def test_publish_requires_execute_before_any_request(self):
        text_file = Path(self.temp.name) / "text.txt"
        text_file.write_text("Original", encoding="utf-8")
        args = ["publish", "--platform", "x", "--state-dir", str(self.state), "--job-id", "one", "--text-file", str(text_file)]
        with mock.patch.dict(p.os.environ, {"X_EXPECTED_USER_ID": "123", "X_USER_ACCESS_TOKEN": "test-token"}, clear=True), \
                mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(p.main(args), 2)

    def test_text_and_state_guards(self):
        for text in ("https://example.com", "example.com", "one@test.com", "xoxb-private-token", "abc.slack.com/archives/a"):
            with self.subTest(text=text), self.assertRaises(p.SafeError):
                p.validate_text("x", text)
        self.assertEqual(len(p.validate_text("x", "한" * 140)), 140)
        with self.assertRaises(p.SafeError):
            p.validate_text("x", "한" * 141)
        self.assertEqual(len(p.validate_text("threads", "한" * 166)), 166)
        with self.assertRaises(p.SafeError):
            p.validate_text("threads", "한" * 167)
        with self.assertRaises(p.SafeError):
            p.state_path(p.SKILLS_ROOT / "runtime")
        with self.assertRaises(p.SafeError):
            p.state_path("relative-state")

    def test_video_preflight_checks_operating_duration_and_format(self):
        path = Path(self.temp.name) / "fixture.mp4"
        path.write_bytes(b"fixture only; ffprobe is mocked")
        for duration in ("29.99", "150.01", "nan", "inf"):
            with self.subTest(duration=duration), mock.patch.object(p.shutil, "which", return_value="/test/ffprobe"), \
                    mock.patch.object(p.subprocess, "run", return_value=mock.Mock(stdout=json.dumps(probe_output(duration)).encode())), \
                    self.assertRaises(p.SafeError):
                p.prepare_video("x", path)
        video = self.video()
        self.assertEqual((video.duration, video.preflight), (30.0, "verified_local_profile"))
        video.path.write_bytes(b"changed final asset")
        pub, client = self.make("x")
        with self.assertRaises(p.SafeError):
            pub.publish("changed", "A product demo", video)
        self.assertEqual(client.calls, [])

    def test_missing_ffprobe_is_visible_and_blocks_video_publication(self):
        path = Path(self.temp.name) / "fixture.mp4"
        path.write_bytes(b"offline fixture")
        with mock.patch.object(p.shutil, "which", return_value=None):
            video = p.prepare_video("x", path)
        self.assertEqual(video.summary()["local_preflight"], "unverified_ffprobe_missing")
        self.assertIsNone(video.duration)
        pub, client = self.make("x")
        with self.assertRaises(p.SafeError):
            pub.publish("one", "A product demo", video)
        self.assertEqual(client.calls, [])

    def test_video_plan_is_offline_and_does_not_claim_remote_asset_match(self):
        video = self.video("threads")
        text_file, unused_state = Path(self.temp.name) / "text.txt", Path(self.temp.name) / "unused-state"
        text_file.write_text("제품 시연", encoding="utf-8")
        args = ["plan", "--platform", "threads", "--state-dir", str(unused_state), "--job-id", "demo",
                "--text-file", str(text_file), "--video-file", str(video.path), "--video-url", video.url]
        output = io.StringIO()
        with mock.patch.dict(p.os.environ, {"THREADS_EXPECTED_USER_ID": "123"}, clear=True), \
                mock.patch.object(p, "prepare_video", return_value=video), \
                mock.patch.object(p.APIClient, "request", side_effect=AssertionError("No network")), \
                contextlib.redirect_stdout(output):
            self.assertEqual(p.main(args), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result["network_requests"], 0)
        self.assertFalse(result["media"]["remote_url_access_and_file_match_verified"])
        self.assertNotIn(video.url, output.getvalue())
        self.assertFalse(unused_state.exists())

    def test_private_video_urls_and_cross_platform_options_rejected(self):
        for url in ("http://media.example.com/a.mp4", "https://user:pass@media.example.com/a.mp4",
                    "https://127.0.0.1/a.mp4", "https://files.slack.com/a.mp4", "https://host.local/a.mp4"):
            with self.subTest(url=url), self.assertRaises(p.SafeError):
                p.public_video_url(url)
        video = self.video()
        with self.assertRaises(p.SafeError):
            p.prepare_video("threads", video.path)
        with self.assertRaises(p.SafeError):
            p.prepare_video("x", video.path, "https://media.example.com/a.mp4")

    def test_wrong_account_blocks_all_video_uploads(self):
        for platform, wrong in (("x", {"data": {"id": "456"}}), ("threads", {"id": "456"})):
            pub, client = self.make(platform, wrong)
            with self.subTest(platform=platform), self.assertRaises(p.SafeError):
                pub.publish("one", "A product demo", self.video(platform))
            self.assertEqual([call[0] for call in client.calls], ["GET"])

    def test_x_binary_chunks_attach_only_finalized_media(self):
        video = self.video(body=b"0123456789", ai=True)
        pub, client = self.make("x", X_ME, X_INIT, {}, {}, {}, {"data": {"id": "700"}}, {"data": {"id": "900"}})
        with mock.patch.object(p, "CHUNK_BYTES", 4):
            result = pub.publish("one", "A product demo", video)
        self.assertEqual(result["state"], "published")
        self.assertEqual([call[1] for call in client.calls], ["/users/me", "/media/upload/initialize",
                         "/media/upload/700/append", "/media/upload/700/append", "/media/upload/700/append",
                         "/media/upload/700/finalize", "/tweets"])
        self.assertEqual([call[4] for call in client.calls[2:5]], [(0, b"0123"), (1, b"4567"), (2, b"89")])
        self.assertEqual(client.calls[-1][2], {"text": "A product demo", "media": {"media_ids": ["700"]}, "made_with_ai": True})
        pub.publish("one", "A product demo", video)
        self.assertEqual(pub.publish("alias", "A product demo", video)["state"], "duplicate")
        self.assertEqual(len(client.calls), 7)

    def test_real_client_binary_multipart_and_redirect_guard(self):
        client = p.APIClient("x", "private-user-token")
        response = mock.Mock(status=204)
        response.read.return_value = b""
        client.opener = mock.Mock()
        client.opener.open.return_value.__enter__ = mock.Mock(return_value=response)
        client.opener.open.return_value.__exit__ = mock.Mock(return_value=False)
        raw = b"\x00\xff\r\nraw-MP4-binary"
        self.assertEqual(client.request("POST", "/media/upload/700/append", multipart=(2, raw)), {})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.x.com/2/media/upload/700/append")
        self.assertIn(b'name="segment_index"\r\n\r\n2\r\n', request.data)
        self.assertIn(b'name="media"; filename="segment.mp4"', request.data)
        self.assertIn(raw, request.data)
        self.assertNotIn(b"private-user-token", request.data)
        self.assertLessEqual(client.opener.open.call_args.kwargs["timeout"], 60)
        self.assertIsNone(p.NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://other.example.com"))
        with self.assertRaises(p.SafeError):
            client.request("POST", "https://other.example.com/upload", multipart=(0, raw))

    def test_x_processing_resumes_after_restart_without_upload_or_post_duplicates(self):
        video = self.video()
        pub, first = self.make("x", X_ME, X_INIT, {},
                               {"data": {"id": "700", "processing_info": {"state": "pending", "check_after_secs": 10}}})
        pending = pub.publish("one", "A product demo", video)
        self.assertEqual((pending["state"], pending["next_poll_at"]), ("media_processing", 1010))
        self.assertTrue(pub.publish("one", "A product demo", video)["network_requests_skipped"])
        self.assertEqual(len(first.calls), 4)
        self.ledger.close()
        self.ledger = p.Ledger(self.state)
        self.now = 1010
        resumed, second = self.make("x", X_ME,
                                    {"data": {"id": "700", "processing_info": {"state": "in_progress", "check_after_secs": 120}}},
                                    X_ME, {"data": {"id": "700", "processing_info": {"state": "succeeded"}}},
                                    {"data": {"id": "900"}})
        self.assertEqual(resumed.publish("one", "A product demo", video)["next_poll_at"], 1130)
        self.now = 1130
        self.assertEqual(resumed.publish("one", "A product demo", video)["state"], "published")
        resumed.publish("one", "A product demo", video)
        self.assertEqual([call[1] for call in second.calls], ["/users/me", "/media/upload", "/users/me", "/media/upload", "/tweets"])
        self.assertEqual(second.calls[1][3], {"command": "STATUS", "media_id": "700"})

    def test_threads_video_uses_public_url_and_explicit_media_ai_flag(self):
        for ai in (False, True):
            video = self.video("threads", body=b"fixture " + str(ai).encode(), ai=ai)
            pub, client = self.make("threads", TH_ME, {"id": "700"}, TH_ME, {"status": "FINISHED"},
                                    {"id": "900"}, *([TH_ME] if ai else []), {"id": "900", "media_type": "VIDEO", "permalink": "https://www.threads.com/@bot/post/abc"})
            job = str(ai)
            pending = pub.publish(job, "A product demo", video)
            self.assertEqual(pending["state"], "media_processing")
            self.assertEqual(client.calls[1][2], {"media_type": "VIDEO", "text": "A product demo",
                                                "video_url": video.url, "is_made_with_ai": str(ai).lower()})
            self.assertEqual(len(client.calls), 2)
            self.now += 60
            self.assertEqual(pub.publish(job, "A product demo", video)["state"], "published")
            self.assertEqual([c[1] for c in client.calls if c[0] == "POST"], ["/123/threads", "/123/threads_publish"])

    def test_media_failure_or_expiry_never_posts(self):
        for case in ("failed", "expired"):
            video = self.video(body=case.encode())
            responses = [X_ME, X_INIT, {}, {"data": {"id": "700", "processing_info": {"state": "pending", "check_after_secs": 10}}}, X_ME]
            if case == "failed":
                responses.append({"data": {"id": "700", "processing_info": {"state": "failed", "error": "remote secret must not escape"}}})
            pub, client = self.make("x", *responses)
            pub.publish(case, "A product demo", video)
            self.now += 10 if case == "failed" else 86401
            result = pub.publish(case, "A product demo", video)
            self.assertEqual(result["state"], "failed")
            self.assertNotIn("remote secret", json.dumps(result))
            self.assertNotIn("/tweets", [c[1] for c in client.calls])

    def test_ambiguous_video_post_is_locked_and_not_retried(self):
        video = self.video()
        pub, client = self.make("x", X_ME, X_INIT, {}, {"data": {"id": "700"}}, p.ApiError())
        self.assertEqual(pub.publish("one", "A product demo", video)["state"], "unknown")
        pub.publish("one", "A product demo", video)
        self.assertEqual(pub.publish("two", "A product demo", video)["state"], "duplicate")
        self.assertEqual(len(client.calls), 5)

    def test_lost_finalize_receipt_resumes_status_not_upload(self):
        video = self.video()
        pub, client = self.make("x", X_ME, X_INIT, {}, p.ApiError(), X_ME,
                                {"data": {"id": "700", "processing_info": {"state": "succeeded"}}}, {"data": {"id": "900"}})
        self.assertEqual(pub.publish("one", "A product demo", video)["state"], "media_processing")
        self.now += 60
        self.assertEqual(pub.publish("one", "A product demo", video)["state"], "published")
        self.assertEqual([c[1] for c in client.calls].count("/media/upload/700/finalize"), 1)
        self.assertEqual([c[1] for c in client.calls].count("/media/upload/initialize"), 1)

    def test_same_job_binds_media_bytes_and_same_text_with_new_media_is_distinct(self):
        first, second = self.video(body=b"first video"), self.video(body=b"second video")
        pub, client = self.make("x", X_ME, X_INIT, {}, {"data": {"id": "700"}}, {"data": {"id": "900"}},
                               X_ME, X_INIT, {}, {"data": {"id": "700"}}, {"data": {"id": "901"}})
        pub.publish("one", "A product demo", first)
        with self.assertRaises(p.SafeError):
            pub.publish("one", "A product demo", second)
        self.assertEqual(pub.publish("two", "A product demo", second)["post_id"], "901")

    def test_concurrent_processing_resume_can_only_post_once(self):
        video = self.video("threads")
        pub, first = self.make("threads", TH_ME, {"id": "700"})
        pub.publish("one", "A product demo", video)
        self.now += 60
        barrier = threading.Barrier(2)

        def resume(_):
            ledger = p.Ledger(self.state)
            client = FakeClient(TH_ME, {"status": "FINISHED"}, {"id": "900"}, p.ApiError())
            publisher = p.Publisher("threads", "123", client, ledger, clock=lambda: self.now)
            try:
                barrier.wait(timeout=5)
                return publisher.publish("one", "A product demo", video), client.calls
            finally:
                ledger.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(resume, (0, 1)))
        calls = [call for _, batch in results for call in batch]
        self.assertEqual(sum(call[1] == "/123/threads_publish" for call in calls), 1)
        self.assertEqual(self.ledger.get(("threads", "123", "one"))["state"], "published")

    def test_processing_stalls_after_five_checks_and_does_not_spin(self):
        video = self.video("threads")
        responses = [TH_ME, {"id": "700"}]
        for _ in range(5):
            responses.extend((TH_ME, {"status": "IN_PROGRESS"}))
        responses.append(TH_ME)
        pub, client = self.make("threads", *responses)
        pub.publish("one", "A product demo", video)
        for _ in range(5):
            self.now += 60
            self.assertEqual(pub.publish("one", "A product demo", video)["state"], "media_processing")
        self.now += 60
        self.assertEqual(pub.publish("one", "A product demo", video)["state"], "processing_stalled")
        count = len(client.calls)
        pub.publish("one", "A product demo", video)
        self.assertEqual(len(client.calls), count)
        self.assertNotIn("/123/threads_publish", [c[1] for c in client.calls])


if __name__ == "__main__":
    unittest.main()
