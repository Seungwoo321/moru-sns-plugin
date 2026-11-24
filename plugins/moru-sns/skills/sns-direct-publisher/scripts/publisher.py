#!/usr/bin/env python3
"""Explicit X/Threads publisher and Instagram Reels. Resume media, never retry an uncertain post."""
import argparse
import fcntl
from datetime import datetime
from dataclasses import dataclass, replace
from fractions import Fraction
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

BASES = {"x": "https://api.x.com/2", "threads": "https://graph.threads.com/v1.0",
         "instagram": "https://graph.instagram.com/v27.0"}
INSTAGRAM_BASES = {"instagram": "https://graph.instagram.com/v27.0",
                   "unified": "https://graph.facebook.com/v27.0"}
INSTAGRAM_UPLOAD_BASE = "https://rupload.facebook.com/ig-api-upload/v27.0"
TOKEN_NAMES = ("X_USER_ACCESS_TOKEN", "THREADS_USER_ACCESS_TOKEN", "INSTAGRAM_USER_ACCESS_TOKEN")
CHUNK_BYTES = 4 * 1024 * 1024
MAX_VIDEO_BYTES = 1_000_000_000
SKILLS_ROOT = Path(__file__).resolve().parents[2]
PRIVATE = re.compile(
    r"xox[baprs]-[\w-]+|github_pat_[\w]+|gh[pousr]_[\w]+|\bsk-[\w-]{16,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bBearer\s+[\w.~+/=-]{10,}"
    r"|\b(?:access_token|client_secret|api[_-]?key|password)\s*[:=]\s*\S+"
    r"|(?:[\w-]+\.)?slack\.com/|slack://"
    r"|[\w.%+-]+@[\w.-]+\.[a-z]{2,}", re.I)
LINK = re.compile(r"[a-z][a-z0-9+.-]*://|www\.|[\w-]+\.[a-z]{2,}\b"
                  r"|\b\d{1,3}(?:\.\d{1,3}){3}\b", re.I)


class SafeError(Exception):
    """Messages are fixed, never copied from a remote response or credential."""


class ApiError(Exception):
    def __init__(self, status=None):
        super().__init__("API request failed.")
        self.status = status


def validate_text(platform, text):
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n")).strip()
    if not text or any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in text):
        raise SafeError("Text is empty or contains unsupported control characters.")
    secrets = [os.environ.get(n, "") for n in TOKEN_NAMES]
    if PRIVATE.search(text) or any(len(s) >= 8 and s in text for s in secrets):
        raise SafeError("Text contains a possible credential, private Slack link, or email address.")
    if platform == "x" and (len(text) > 140 or LINK.search(text)):
        raise SafeError("This version accepts X plain text only: <=140 Unicode codepoints; no link-like fragments.")
    if platform == "threads" and len(text.encode("utf-8")) > 500:
        raise SafeError("This version conservatively limits Threads text to 500 UTF-8 bytes, including Korean.")
    if platform == "instagram" and len(text) > 2200:
        raise SafeError("Instagram captions must be at most 2200 Unicode codepoints in this tool.")
    return text


def state_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise SafeError("Private paths must be absolute and cannot contain parent traversal.")
    parts = [*reversed(path.parents), path]
    if any(part.is_symlink() for part in parts):
        raise SafeError("Symlinks are not allowed in private paths.")
    if path.is_relative_to(SKILLS_ROOT):
        raise SafeError("Private paths must stay outside the skills checkout.")
    if any((part / ".git").exists() or (part / ".git").is_symlink() for part in parts):
        raise SafeError("Private paths must stay outside every Git checkout.")
    # Installed plugin caches may not contain .git. Keep private files outside
    # the entire plugin payload as well as its skills directory.
    if any((part / "plugin.json").is_file() or (part / ".codex-plugin" / "plugin.json").is_file()
           for part in parts):
        raise SafeError("Private paths must stay outside installed plugin payloads.")
    return path


def check_state_node(path, directory=False):
    info = path.lstat()
    expected_type = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected_type(info.st_mode) or info.st_uid != os.getuid() or
            stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600) or
            (not directory and info.st_nlink != 1)):
        raise SafeError("Private runtime state is not intact.")
    return info


def file_identity(info):
    return {"device": info.st_dev, "inode": info.st_ino}


def read_state_marker(path):
    info = check_state_node(path)
    if info.st_size > 65536:
        raise SafeError("Runtime marker is invalid.")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        if file_identity(os.fstat(source.fileno())) != file_identity(info):
            raise SafeError("Runtime marker changed.")
        data = json.load(source)
    if (not isinstance(data, dict) or data.get("version") not in (1, "1-recovered") or
            not isinstance(data.get("runtime_id"), str) or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", data["runtime_id"]) or
            not isinstance(data.get("accounts"), dict) or not data["accounts"]):
        raise SafeError("Runtime marker is invalid.")
    if (data["version"] == "1-recovered") != ("recovery" in data):
        raise SafeError("Recovery marker requires a recovery-aware v1 executor.")
    for platform, binding in data["accounts"].items():
        if (platform not in BASES or not isinstance(binding, dict) or
                not isinstance(binding.get("account_id"), str) or
                not re.fullmatch(r"[0-9]+", binding["account_id"]) or
                not isinstance(binding.get("username"), str) or
                not re.fullmatch(r"[A-Za-z0-9_.]{1,64}", binding["username"]) or
                (platform == "instagram" and binding.get("auth_mode") not in INSTAGRAM_BASES) or
                (platform != "instagram" and binding.get("auth_mode") is not None)):
            raise SafeError("Runtime account binding is invalid.")
    return data


def inspect_state(directory, runtime_id=None, bindings=None):
    path = state_path(directory)
    check_state_node(path.parent, directory=True)
    check_state_node(path, directory=True)
    marker = read_state_marker(path / "runtime.json")
    if ((runtime_id is not None and marker["runtime_id"] != runtime_id) or
            (bindings is not None and marker["accounts"] != bindings)):
        raise SafeError("Runtime marker or configured accounts do not match.")
    database = path / "publisher.sqlite3"
    info = check_state_node(database)
    if marker.get("ledger_identity") != file_identity(info):
        raise SafeError("The ledger was replaced; inspect its history before resuming.")
    fd = os.open(database, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        if file_identity(os.fstat(fd)) != file_identity(info):
            raise SafeError("The ledger changed during inspection.")
        header = os.read(fd, 20)
    finally:
        os.close(fd)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
        raise SafeError("Unexpected ledger format or WAL mode.")
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise SafeError("Unexpected ledger journal mode.")
        required = {"platform", "account_id", "job_id", "content_hash", "state", "post_id", "auth_mode", "upload_mode"}
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)] or not required.issubset(columns):
            raise SafeError("Ledger integrity or schema validation failed.")
        if "recovery" in marker:
            recovery = marker["recovery"]
            if not isinstance(recovery, dict) or recovery.get("version") != 1:
                raise SafeError("Invalid recovery provenance.")
            evidence = db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()
            if not evidence or hashlib.sha256(evidence[0].encode()).hexdigest() != recovery.get("sha256"):
                raise SafeError("Recovery provenance mismatch.")
        jobs = db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        db.close()
    if file_identity(check_state_node(database)) != file_identity(info):
        raise SafeError("The ledger changed during inspection.")
    return jobs


def require_state_binding(directory, platform, account_id, auth_mode=None):
    inspect_state(directory)
    binding = read_state_marker(state_path(directory) / "runtime.json")["accounts"].get(platform)
    if not binding or binding["account_id"] != account_id or binding.get("auth_mode") != auth_mode:
        raise SafeError("The configured account does not match the existing runtime state.")


def public_video_url(value):
    """Syntax-only check; never fetch arbitrary media URLs or forward credentials."""
    try:
        parsed = urllib.parse.urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or not host or "." not in host or parsed.username or
                parsed.password or parsed.fragment or parsed.port not in (None, 443) or
                any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value or
                host.endswith((".local", ".localhost", ".internal", ".test", ".invalid")) or
                host in ("slack.com", "slack-files.com") or host.endswith((".slack.com", ".slack-files.com"))):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        if any(os.environ.get(n) and os.environ[n] in value
               for n in TOKEN_NAMES):
            raise ValueError()
    except (TypeError, ValueError):
        raise SafeError("Meta video requires a public HTTPS URL without credentials, fragments or private Slack access.") from None
    return value


def file_stamp(path):
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class Image:
    path: Path
    sha256: str
    size: int
    stamp: tuple
    width: int
    height: int
    url: object = None
    ai_generated: bool = False
    media_type: str = 'IMAGE'
    preflight: str = 'verified_local_image_profile'

    def verify_for_publish(self):
        if self.preflight != 'verified_local_image_profile' or file_stamp(self.path) != self.stamp:
            raise SafeError('Approved image changed after inspection.')

    def summary(self):
        return {'type':'image','sha256':self.sha256,'bytes':self.size,'width':self.width,'height':self.height,
                'local_preflight':self.preflight,'public_image_url_provided':bool(self.url)}


def prepare_image(image_file, image_url=None, *, allow_missing_url=False):
    if not image_url and not allow_missing_url:
        raise SafeError('Image publication requires its approved official URL.')
    if image_url:
        public_video_url(image_url)
    path = Path(image_file).expanduser().resolve()
    stamp = file_stamp(path)
    if path.suffix.lower() not in ('.jpg','.jpeg') or not 0 < stamp[2] <= 8_000_000:
        raise SafeError('Image fallback requires a bounded local JPEG.')
    try:
        data = path.read_bytes()
        if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
            raise ValueError()
        result = subprocess.run(['ffprobe','-v','error','-show_entries','stream=codec_name,width,height','-of','json',str(path)],
                                capture_output=True,check=True,timeout=30)
        streams = json.loads(result.stdout)['streams']
        if len(streams)!=1 or streams[0]['codec_name']!='mjpeg':
            raise ValueError()
        width,height = int(streams[0]['width']),int(streams[0]['height'])
        if not (320 <= width <= 1440 and 0 < height and max(width/height,height/width) <= 10):
            raise ValueError()
    except Exception:
        raise SafeError('JPEG inspection failed.') from None
    if file_stamp(path) != stamp:
        raise SafeError('JPEG changed during inspection.')
    return Image(path,hashlib.sha256(data).hexdigest(),len(data),stamp,width,height,image_url)


@dataclass(frozen=True)
class Video:
    path: Path
    sha256: str
    size: int
    stamp: tuple
    duration: object
    preflight: str
    url: object = None
    ai_generated: bool = False
    video_kind: str = "real-demo"
    audio_kind: str = "original"

    def verify_for_publish(self):
        if self.preflight != "verified_local_profile":
            raise SafeError("Video publishing requires ffprobe verification; install ffprobe and rerun plan.")
        if file_stamp(self.path) != self.stamp:
            raise SafeError("Video changed after preflight; run plan again with the final file.")

    def summary(self):
        return {"type": "video", "sha256": self.sha256, "bytes": self.size,
                "duration_seconds": self.duration, "local_preflight": self.preflight,
                "ai_generated_media": self.ai_generated,
                "video_kind": self.video_kind, "audio_kind": self.audio_kind,
                "public_video_url_provided": self.url is not None,
                "remote_url_access_and_file_match_verified": False if self.url else None,
                "profile_note": "Local profile: MP4/H.264, yuv420p, 23-60 fps, width <=1920, optional AAC, 30-150 seconds, <=1 GB. This is an operating profile, not the platforms' full limits.",
                "verification_note": "Local metadata checks do not verify visual privacy, all encoding details, remote URL access or platform acceptance. URL uploads must serve these same immutable bytes publicly; explicit Instagram Unified resumable uploads send the local bytes directly."}


def prepare_video(platform, video_file=None, video_url=None, ai_generated=False,
                  video_kind="real-demo", audio_kind="original", *, allow_missing_url=False):
    if not video_file:
        if video_url or ai_generated or video_kind != "real-demo" or audio_kind != "original":
            raise SafeError("--video-url and --ai-generated-media require --video-file.")
        return None
    if platform in ("threads", "instagram") and not video_url and not allow_missing_url:
        raise SafeError("Threads/Instagram video requires both --video-file and --video-url for the same immutable asset.")
    if platform == "x" and video_url:
        raise SafeError("X uploads --video-file directly; --video-url is only for Threads/Instagram.")
    url = public_video_url(video_url) if video_url else None
    path = Path(video_file).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".mp4":
        raise SafeError("--video-file must name a local MP4 file.")
    stamp = file_stamp(path)
    size = stamp[2]
    if not 0 < size <= MAX_VIDEO_BYTES:
        raise SafeError("This video profile requires a nonempty MP4 of at most 1 GB.")
    if platform == "instagram" and size > 300_000_000:
        raise SafeError("Instagram Reels requires a video of at most 300 MB.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    duration, preflight = None, "unverified_ffprobe_missing"
    executable = shutil.which("ffprobe")
    if executable:
        try:
            result = subprocess.run(
                [executable, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
                 "format=format_name,duration:stream=codec_type,codec_name,width,pix_fmt,avg_frame_rate,field_order,sample_rate,channels",
                 "-of", "json", str(path)], capture_output=True, check=True, timeout=30)
            data = json.loads(result.stdout)
            duration = float(data["format"]["duration"])
            videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
            audios = [s for s in data["streams"] if s.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) > 1:
                raise ValueError()
            stream = videos[0]
            fps = float(Fraction(stream["avg_frame_rate"]))
            if ("mp4" not in data["format"]["format_name"].split(",") or
                    not math.isfinite(duration) or not 30 <= duration <= 150 or
                    stream.get("codec_name") != "h264" or stream.get("pix_fmt") != "yuv420p" or
                    not 23 <= fps <= 60 or not 1 <= int(stream["width"]) <= 1920 or
                    stream.get("field_order") not in (None, "unknown", "progressive") or
                    any(s.get("codec_name") != "aac" or not 0 < int(s.get("sample_rate", 0)) <= 48000 or
                        int(s.get("channels", 0)) not in (1, 2) for s in audios)):
                raise ValueError()
            preflight = "verified_local_profile"
        except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, ZeroDivisionError):
            raise SafeError("Video failed local ffprobe profile checks (MP4/H.264/AAC, 23-60 fps, width <=1920, 30-150 seconds).") from None
    if stamp != file_stamp(path):
        raise SafeError("Video changed while being checked; use an immutable final asset.")
    contains_ai = ai_generated or video_kind.startswith("ai-") or audio_kind.startswith("ai-")
    return Video(path, digest.hexdigest(), size, stamp, duration, preflight, url, contains_ai, video_kind, audio_kind)


def content_hash(text, video=None, reply_to_id=None, *, ai_generated_text=False, publication_binding=None):
    # Preserve text-only receipts from the previous version.
    body = text.encode() if video is None else json.dumps(
        {"text": text, ("image_sha256" if getattr(video,'media_type',None)=='IMAGE' else "video_sha256"): video.sha256, "ai_generated_media": video.ai_generated},
        sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    if video is None and ai_generated_text:
        body = b"ai-generated-text:true:" + body
    if publication_binding is not None:
        body = b"publication-profile:" + publication_binding.encode() + b":" + body
    if reply_to_id:
        body = b"reply:" + reply_to_id.encode() + b":" + body
    return hashlib.sha256(body).hexdigest()


def require_root_receipt(row):
    if not row or row["state"] != "published" or not row.get("post_id") or row.get("reply_to_id"):
        raise SafeError("A follow-up requires this account's saved, successfully published root post; replies cannot be parents.")
    return api_id(row["post_id"])


def read_reply_parent(directory, account_id, job_id):
    """Read an existing receipt for offline plan without creating or changing a DB."""
    path = state_path(directory) / "publisher.sqlite3"
    if not path.is_file() or path.is_symlink():
        raise SafeError("A follow-up plan requires an existing published root receipt.")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM jobs WHERE platform='threads' AND account_id=? AND job_id=?",
                         (account_id, job_id)).fetchone()
        return require_root_receipt(dict(row) if row else None)


def api_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ApiError()
    return value


def api_dict(value):
    if not isinstance(value, dict):
        raise ApiError()
    return value


def instagram_upload_uri(container_id, value):
    expected = f"{INSTAGRAM_UPLOAD_BASE}/{api_id(container_id)}"
    if not isinstance(value, str) or value != expected:
        raise SafeError("Instagram upload URI does not match the fixed host, version and container ID.")
    return expected


class VideoUploadBody:
    """Stream bounded local bytes while independently checking their preflight hash."""
    def __init__(self, source, video):
        self.source, self.video = source, video
        self.count, self.digest = 0, hashlib.sha256()

    def read(self, size=-1):
        remaining = self.video.size - self.count
        data = self.source.read(min(size, remaining + 1) if size >= 0 else remaining + 1)
        if self.count + len(data) > self.video.size:
            raise SafeError("Video changed during upload; the uploaded media was not published.")
        self.count += len(data)
        self.digest.update(data)
        return data

    def verify_complete(self):
        if (self.count != self.video.size or self.digest.hexdigest() != self.video.sha256 or
                file_stamp(self.video.path) != self.video.stamp):
            raise SafeError("Video changed during upload; the uploaded media was not published.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class APIClient:
    def __init__(self, platform, token, instagram_login_mode=None):
        self.platform, self.token = platform, token
        if platform == "instagram" and instagram_login_mode not in INSTAGRAM_BASES:
            raise SafeError("Instagram requires an explicit login mode: unified or instagram.")
        self.instagram_login_mode = instagram_login_mode
        self.base = INSTAGRAM_BASES[instagram_login_mode] if platform == "instagram" else BASES[platform]
        self.opener = urllib.request.build_opener(NoRedirect())

    def upload_instagram_video(self, container_id, uri, video):
        if self.platform != "instagram" or self.instagram_login_mode != "unified":
            raise SafeError("Direct Instagram video upload requires explicit Unified Login.")
        uri = instagram_upload_uri(container_id, uri)
        video.verify_for_publish()
        if video.url is not None or not 0 < video.size <= 300_000_000:
            raise SafeError("Direct Instagram upload requires a local Reel of at most 300 MB and no video URL.")
        try:
            with video.path.open("rb") as source:
                body = VideoUploadBody(source, video)
                request = urllib.request.Request(uri, data=body, method="POST", headers={
                    "Authorization": "OAuth " + self.token, "offset": "0", "file_size": str(video.size),
                    "Content-Length": str(video.size), "Content-Type": "application/octet-stream",
                    "Accept": "application/json"})
                with self.opener.open(request, timeout=120) as response:
                    result = json.loads(response.read(1_000_001))
                body.verify_complete()
            if not isinstance(result, dict) or result.get("success") is not True or result.get("error") or result.get("errors"):
                raise ApiError()
            return {"success": True}
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            raise ApiError() from None

    def request(self, method, path, payload=None, params=None, multipart=None):
        if method not in ("GET", "POST") or not re.fullmatch(r"/[A-Za-z0-9_/]+", path):
            raise SafeError("Unsupported API request.")
        query, headers, body = dict(params or {}), {"Accept": "application/json"}, None
        if multipart is not None:
            if (self.platform != "x" or method != "POST" or payload is not None or params or
                    not re.fullmatch(r"/media/upload/[0-9]+/append", path)):
                raise SafeError("Multipart upload is only supported by the fixed X append endpoint.")
            segment, chunk = multipart
            if type(segment) is not int or not 0 <= segment <= 9999 or not isinstance(chunk, bytes) or not 0 < len(chunk) <= CHUNK_BYTES:
                raise SafeError("Invalid upload segment.")
            boundary = "sns-publisher-" + secrets.token_hex(24)
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"segment_index\"\r\n\r\n{segment}\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"media\"; filename=\"segment.mp4\"\r\n"
                    "Content-Type: application/octet-stream\r\n\r\n").encode() + chunk + f"\r\n--{boundary}--\r\n".encode()
            headers.update({"Authorization": "Bearer " + self.token, "Content-Type": "multipart/form-data; boundary=" + boundary})
        elif self.platform == "x":
            headers["Authorization"] = "Bearer " + self.token
            if payload is not None:
                body = json.dumps(payload).encode("utf-8")
                headers["Content-Type"] = "application/json"
        elif method == "POST":
            body = urllib.parse.urlencode({**(payload or {}), "access_token": self.token}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            query["access_token"] = self.token
        url = self.base + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            with self.opener.open(req, timeout=20) as response:
                raw = response.read(1_000_001)
                if multipart is not None and not raw and response.status in (200, 201, 204):
                    return {}
                data = json.loads(raw)
            if not isinstance(data, dict) or data.get("error") or data.get("errors"):
                raise ApiError()
            return data
        except urllib.error.HTTPError as exc:
            raise ApiError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            raise ApiError() from None


class Ledger:
    def __init__(self, directory, *, initialize=False):
        directory = state_path(directory)
        check_state_node(directory.parent, directory=True)
        check_state_node(directory, directory=True)
        path = directory / "publisher.sqlite3"
        if initialize:
            # Only runtime_guard's explicit, exclusive initialization calls this.
            # Never reuse an existing database, even when it is empty.
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
            os.close(fd)
        else:
            inspect_state(directory)
        self.review_history = None
        self.pair_context = None
        self.directory = directory
        self.recovery_lock = None
        self.recovered = not initialize and "recovery" in read_state_marker(directory / "runtime.json")
        if self.recovered:
            self.recovery_lock = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                fcntl.flock(self.recovery_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if file_identity(os.fstat(self.recovery_lock)) != file_identity(check_state_node(path)):
                    raise SafeError("Recovery ledger changed.")
                inspect_state(directory)
            except Exception:
                os.close(self.recovery_lock)
                self.recovery_lock = None
                raise
        try:
            self.db = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10, isolation_level=None)
        except Exception:
            if self.recovery_lock is not None:
                os.close(self.recovery_lock)
                self.recovery_lock = None
            raise
        self.db.row_factory = sqlite3.Row
        if not initialize:
            return
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                platform TEXT, account_id TEXT, job_id TEXT, content_hash TEXT,
                state TEXT, container_id TEXT, post_id TEXT, permalink TEXT,
                duplicate_of TEXT, http_status INTEGER, updated_at REAL,
                PRIMARY KEY(platform, account_id, job_id));
            CREATE UNIQUE INDEX IF NOT EXISTS once_per_content
                ON jobs(platform, account_id, content_hash)
                WHERE state NOT IN ('failed', 'duplicate');
        """)
        # Add resumable media state without invalidating existing text receipts.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
            for column, kind in (("media_id", "TEXT"), ("media_expires_at", "REAL"),
                                 ("next_poll_at", "REAL"), ("poll_count", "INTEGER"), ("reply_to_id", "TEXT"),
                                 ("auth_mode", "TEXT"), ("upload_mode", "TEXT")):
                if column not in columns:
                    self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {kind}")
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()
        if self.recovery_lock is not None:
            os.close(self.recovery_lock)
            self.recovery_lock = None

    def get(self, key):
        row = self.db.execute("SELECT * FROM jobs WHERE platform=? AND account_id=? AND job_id=?", key).fetchone()
        return dict(row) if row else None

    def has_review(self, key):
        if not self.recovered:
            return False
        if not self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_reviews'").fetchone():
            return False
        return bool(self.db.execute('SELECT 1 FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?', key).fetchone())

    def prepare_review(self, key, client, clock=time.time, video=None, upload_mode=None, pair_id=None):
        import candidate_review as review
        self.review_history = None
        self.image_delivery_check = None
        marker = read_state_marker(self.directory / 'runtime.json')
        rows = review.collect(marker['accounts'], {key[0]: client})
        review.match_ledger(self, rows)
        for item in rows:
            if (item['platform'], item['account_id']) == key[:2] and clock() - item['published_at'] < 21600:
                raise SafeError('Official account history is within the six-hour publication interval.')
        saved = self.db.execute('SELECT review_id,document,document_sha256 FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?', key).fetchone()
        review.require(saved and review.digest(saved['document']) == saved['document_sha256'])
        doc = json.loads(saved['document'])
        review.validate_document(doc, marker)
        self.pair_context = None
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='pair_review_bindings'").fetchone():
            scoped = self.db.execute('SELECT pair_id FROM pair_review_bindings WHERE review_id=?',(saved['review_id'],)).fetchone()
            review.require((scoped is None and pair_id is None) or (scoped is not None and scoped[0] == pair_id))
        if pair_id is not None:
            import candidate_pair
            candidate_pair.check(self,pair_id,doc,rows)
            self.pair_context = (pair_id,key,doc,rows)

        review.require(video is not None)
        if key[0] == 'instagram':
            review.require(upload_mode == 'resumable' and video.url is None)
        import delivery_binding
        checked_url = delivery_binding.verify(self, doc, video=video)
        if isinstance(video, Image):
            review.require(isinstance(checked_url, str) and bool(checked_url))
            video = replace(video, url=checked_url)
            self.image_delivery_check = (key, saved["document_sha256"], video.sha256, review.digest(checked_url))
        self.review_history = (key, review.snapshot_digest(rows))
        return video

    def claim(self, key, digest, reply_to_id=None, auth_mode=None, upload_mode=None, media_sha256=None):
        return self._claim(key,digest,reply_to_id,auth_mode,upload_mode,media_sha256)

    def claim_pair(self, pair_id, key, digest, reply_to_id=None, auth_mode=None, upload_mode=None, media_sha256=None):
        if not self.recovered or reply_to_id is not None:
            raise SafeError('One-pair admission requires a recovered root-video job.')
        return self._claim(key,digest,reply_to_id,auth_mode,upload_mode,media_sha256,pair_id)

    def _claim(self, key, digest, reply_to_id=None, auth_mode=None, upload_mode=None, media_sha256=None, pair_id=None):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.get(key)
            if self.recovered:
                provenance = json.loads(self.db.execute("SELECT value FROM recovery_metadata WHERE key='provenance'").fetchone()[0])
                if pair_id is None:
                    if not provenance["inventory_complete"] or not provenance["old_writers_stopped"]:
                        raise SafeError("Recovery ownership or attempt inventory is incomplete.")
                    if self.db.execute("SELECT 1 FROM jobs WHERE state NOT IN ('published','failed','duplicate') AND NOT (platform=? AND account_id=? AND job_id=? AND state='media_processing') LIMIT 1", key).fetchone():
                        raise SafeError("Unresolved recovery or publication attempt; reconcile before retry.")
                else:
                    import candidate_pair
                    if not self.pair_context or self.pair_context[:2] != (pair_id,key):
                        raise SafeError('One-pair admission requires exact fresh review context.')
                    candidate_pair.check(self,pair_id,self.pair_context[2],self.pair_context[3])
                review_id = None
                historical_unknown = self.db.execute("SELECT 1 FROM recovery_posts WHERE content_hash IS NULL OR (media_type!='TEXT_POST' AND media_sha256 IS NULL) LIMIT 1").fetchone()
                if historical_unknown or self.has_review(key):
                    if not self.has_review(key) or not self.review_history or self.review_history[0] != key:
                        raise SafeError("Historical content or original media identity is unverified; exact candidate review and fresh history required.")
                    import candidate_review as review
                    review_id = review.authorize(self, key, digest, media_sha256, self.review_history[1], old)
                if old:
                    if old['content_hash'] != digest:
                        raise SafeError("Recovered job ID is bound to different content.")
                    if old.get("auth_mode") != auth_mode or old.get("upload_mode") != upload_mode:
                        raise SafeError("Recovered job execution mode differs; reconcile without resubmitting.")
                    self.db.execute("COMMIT")
                    return old, False
                if self.db.execute("SELECT 1 FROM jobs WHERE platform=? AND account_id=? AND content_hash=?", (*key[:2], digest)).fetchone():
                    raise SafeError("Content was already attempted; a new ID cannot bypass history.")
                if media_sha256 and self.db.execute("SELECT 1 FROM recovery_media WHERE platform=? AND sha256=?",(key[0],media_sha256)).fetchone():
                    raise SafeError("Media was already attempted.")
                last = self.db.execute("SELECT MAX(updated_at) FROM jobs WHERE platform=? AND account_id=? AND state='published'",key[:2]).fetchone()[0]
                if last is not None and time.time()-last < 21600:
                    raise SafeError("Six-hour publication interval has not elapsed.")
                if media_sha256:
                    self.db.execute("INSERT INTO recovery_media VALUES(?,?,?)",(key[0],media_sha256,key[2]))
                if review_id:
                    changed = self.db.execute("UPDATE recovery_reviews SET state='consumed' WHERE review_id=? AND state='approved'", (review_id,)).rowcount
                    if changed != 1:
                        raise SafeError('Candidate review was already consumed.')
            if old:
                if old["content_hash"] != digest:
                    raise SafeError("This job ID is already bound to different text or media.")
                if old.get("auth_mode") != auth_mode:
                    raise SafeError("This job's login mode is different or was not recorded. Reconcile its existing receipt; do not resubmit.")
                if old.get("upload_mode") != upload_mode:
                    raise SafeError("This job's upload mode is different or was not recorded. Reconcile its existing receipt; do not resubmit.")
                self.db.execute("COMMIT")
                return old, False
            duplicate = self.db.execute(
                "SELECT job_id FROM jobs WHERE platform=? AND account_id=? AND content_hash=? "
                "AND state NOT IN ('failed', 'duplicate')", (*key[:2], digest)).fetchone()
            self.db.execute("INSERT INTO jobs(platform,account_id,job_id,content_hash,state,duplicate_of,updated_at,reply_to_id,auth_mode,upload_mode) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?)", (*key, digest, "duplicate" if duplicate else "claimed",
                                                      duplicate[0] if duplicate else None, time.time(), reply_to_id, auth_mode, upload_mode))
            self.db.execute("COMMIT")
            return self.get(key), not bool(duplicate)
        except Exception:
            if self.db.in_transaction:
                self.db.execute("ROLLBACK")
            raise
        finally:
            self.review_history = None
            self.pair_context = None

    def update(self, key, state, **fields):
        fields.update(state=state, updated_at=time.time())
        self.db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) +
                        " WHERE platform=? AND account_id=? AND job_id=?", (*fields.values(), *key))

    def acquire_processing(self, key, now):
        """A single worker can advance a due media job towards its public POST."""
        cursor = self.db.execute(
            "UPDATE jobs SET state='checking_media', updated_at=? "
            "WHERE platform=? AND account_id=? AND job_id=? AND state='media_processing' AND next_poll_at<=?",
            (now, *key, now))
        return cursor.rowcount == 1


class Publisher:
    def __init__(self, platform, expected_id, client, ledger, wait=time.sleep, clock=time.time,
                 instagram_login_mode=None, instagram_upload_mode=None, candidate_pair_id=None, ai_generated_text=False, approved_source_kind=None, publication_evidence=None, source_file=None, reviewed_hash=None):
        self.ai_generated_text = ai_generated_text
        self.approved_source_kind = approved_source_kind
        self.publication_evidence = publication_evidence
        self.source_file = source_file
        self.reviewed_hash = reviewed_hash
        self.candidate_pair_id = candidate_pair_id
        self.platform, self.expected, self.client, self.ledger, self.wait = platform, expected_id, client, ledger, wait
        self.clock = clock
        if platform == "instagram" and instagram_login_mode not in INSTAGRAM_BASES:
            raise SafeError("Instagram requires an explicit login mode: unified or instagram.")
        if platform == "instagram" and isinstance(client, APIClient) and client.instagram_login_mode != instagram_login_mode:
            raise SafeError("Instagram client and publisher login modes must match.")
        self.instagram_login_mode = instagram_login_mode
        if instagram_upload_mode is not None and (platform != "instagram" or instagram_login_mode != "unified" or
                                                   instagram_upload_mode != "resumable"):
            raise SafeError("--instagram-upload-mode resumable requires Instagram Unified Login.")
        self.instagram_upload_mode = instagram_upload_mode

    def check_unified(self):
        """BISU /me is not the IG identity. Only use the fixed assigned-accounts edge."""
        params, cursors = {"fields": "id,username"}, set()
        for _ in range(10):
            result = api_dict(self.client.request("GET", "/me/assigned_instagram_accounts", params=params))
            accounts = result.get("data")
            if not isinstance(accounts, list):
                raise ApiError()
            for account in accounts:
                if api_id(api_dict(account).get("id")) == self.expected:
                    return {"platform": "instagram", "account_id": self.expected, "account_matches": True,
                            "auth_mode": "unified"}
            paging = api_dict(result.get("paging", {}))
            after = api_dict(paging.get("cursors", {})).get("after")
            if not after:
                break
            if not isinstance(after, str) or len(after) > 4096 or after in cursors or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", after):
                raise SafeError("Assigned-account pagination is invalid or repeated; no publication is allowed.")
            cursors.add(after)
            # Never follow a server-provided next URL or allow it to choose a host.
            params = {"fields": "id,username", "after": after}
        raise SafeError("The expected Instagram account was not confirmed in assigned accounts; no publication is allowed.")

    def check(self):
        if self.platform == "instagram" and self.instagram_login_mode == "unified":
            return self.check_unified()
        path = "/users/me" if self.platform == "x" else "/me"
        params = None if self.platform == "x" else {"fields": "user_id,username" if self.platform == "instagram" else "id,username"}
        result = api_dict(self.client.request("GET", path, params=params))
        profile = api_dict(result.get("data")) if self.platform == "x" else result
        if self.platform == "instagram" and "data" in result:
            data = result["data"]
            if not isinstance(data, list) or len(data) != 1:
                raise ApiError()
            profile = api_dict(data[0])
        identity = profile.get("user_id") if self.platform == "instagram" else profile.get("id")
        if api_id(identity) != self.expected:
            raise SafeError("Authenticated account does not match the required expected account ID.")
        result = {"platform": self.platform, "account_id": self.expected, "account_matches": True}
        if self.platform == "instagram":
            result["auth_mode"] = "instagram"
        return result

    def pending(self, key, delay=60, error=None):
        self.ledger.update(key, "media_processing", next_poll_at=self.clock() + max(1, delay))
        result = {**self.ledger.get(key), "resume_note": "Rerun the same publish command and job ID after next_poll_at. No scheduler is installed."}
        if error:
            result["error"] = error
        return result

    def x_processing(self, key, result, allow_missing=False):
        data = api_dict(api_dict(result).get("data"))
        row = self.ledger.get(key)
        if api_id(data.get("id")) != row["media_id"]:
            raise ApiError()
        if "expires_after_secs" in data:
            seconds = data["expires_after_secs"]
            if type(seconds) is not int or seconds <= 0:
                raise ApiError()
            self.ledger.update(key, "checking_media", media_expires_at=self.clock() + seconds)
        info = data.get("processing_info")
        if info is None and allow_missing:
            return None
        info = api_dict(info)
        state = info.get("state")
        if state == "succeeded":
            return None
        if state == "failed":
            self.ledger.update(key, "failed")
            return {**self.ledger.get(key), "error": "Remote video processing failed; nothing was published."}
        if state not in ("pending", "in_progress"):
            raise ApiError()
        delay = info.get("check_after_secs", 60)
        if type(delay) is not int or not 0 <= delay <= 86400:
            raise ApiError()
        return self.pending(key, max(1, delay))

    def poll(self, key):
        """One read per invocation; read failures can resume, public POST failures cannot."""
        row = self.ledger.get(key)
        if row["media_expires_at"] and self.clock() >= row["media_expires_at"]:
            self.ledger.update(key, "failed")
            return {**self.ledger.get(key), "error": "Media expired before publication; nothing was published by this job."}
        if (row["poll_count"] or 0) >= 5:
            self.ledger.update(key, "processing_stalled")
            return {**self.ledger.get(key), "error": "Processing has not completed after five checks. Reconcile this asset before continuing."}
        self.ledger.update(key, "checking_media", poll_count=(row["poll_count"] or 0) + 1)
        try:
            if self.platform == "x":
                result = self.client.request("GET", "/media/upload", params={"command": "STATUS", "media_id": api_id(row["media_id"])})
                return self.x_processing(key, result)
            field = "status_code" if self.platform == "instagram" else "status"
            result = api_dict(self.client.request("GET", f"/{api_id(row['container_id'])}",
                                                 params={"fields": field if self.platform == "instagram" else "status,error_message"}))
            state = result.get(field)
            if state == "FINISHED":
                return None
            if state in ("ERROR", "EXPIRED"):
                self.ledger.update(key, "failed")
                return {**self.ledger.get(key), "error": "Meta container failed or expired; nothing was published by this job."}
            if state == "PUBLISHED":
                self.ledger.update(key, "unknown")
                return {**self.ledger.get(key), "error": "Container is already published but no local post receipt is available. Reconcile it; do not repost."}
            if state != "IN_PROGRESS":
                raise ApiError()
            return self.pending(key)
        except ApiError:
            return self.pending(key, error="Media status check failed; the same job may resume. No public POST was attempted.")

    def upload_x(self, key, video):
        result = api_dict(self.client.request("POST", "/media/upload/initialize",
                                             {"media_type": "video/mp4", "total_bytes": video.size, "media_category": "tweet_video"}))
        data = api_dict(result.get("data"))
        media_id = api_id(data.get("id"))
        expiry = data.get("expires_after_secs")
        if type(expiry) is not int or expiry <= 0:
            raise ApiError()
        self.ledger.update(key, "uploading", media_id=media_id, media_expires_at=self.clock() + expiry)
        uploaded, digest = 0, hashlib.sha256()
        with video.path.open("rb") as source:
            segment = 0
            for chunk in iter(lambda: source.read(CHUNK_BYTES), b""):
                digest.update(chunk)
                uploaded += len(chunk)
                self.client.request("POST", f"/media/upload/{media_id}/append", multipart=(segment, chunk))
                segment += 1
        if uploaded != video.size or digest.hexdigest() != video.sha256 or file_stamp(video.path) != video.stamp:
            raise SafeError("Video changed during upload; the uploaded media was not published.")
        # Finalize is not public publication. If its response is lost, query this
        # media ID later; never initialize/upload it again under the same job.
        self.ledger.update(key, "checking_media")
        try:
            result = self.client.request("POST", f"/media/upload/{media_id}/finalize")
            return self.x_processing(key, result, allow_missing=True)
        except ApiError as exc:
            if exc.status and 400 <= exc.status < 500 and exc.status not in (408, 409):
                raise
            return self.pending(key, error="Finalize receipt was unavailable. Resume this job to check its existing media ID.")

    def confirm_profile_receipt(self, key, media, profile, *, resumed=True):
        """A recorded POST is confirmed only by GET; label uncertainty cannot POST."""
        row=self.ledger.get(key)
        if not row or row['state'] != 'confirming_label':
            raise SafeError('No pending profile receipt exists.')
        post_id=api_id(row['post_id'])
        fields='id,permalink,media_type'+(',is_ai_generated' if self.platform=='instagram' else '')
        try:
            self.check()
            receipt=api_dict(self.client.request('GET','/'+post_id,params={'fields':fields}))
        except (ApiError,SafeError):
            return {**self.ledger.get(key),'publication_requests_skipped':resumed,'error':'Receipt or AI label unconfirmed; GET-only confirmation required.'}
        expected_type='IMAGE' if isinstance(media,Image) else 'VIDEO' if media else 'TEXT_POST'
        pattern=r'https://(?:www\.)?instagram\.com/[^\s]+' if self.platform=='instagram' else r'https://(?:www\.)?threads\.(?:com|net)/[^\s]+'
        url=receipt.get('permalink')
        if (receipt.get('id') != post_id or receipt.get('media_type') != expected_type or
                not isinstance(url,str) or not re.fullmatch(pattern,url) or
                (self.platform=='instagram' and (type(receipt.get('is_ai_generated')) is not bool or receipt['is_ai_generated'] != profile['ai_disclosure']))):
            return {**self.ledger.get(key),'publication_requests_skipped':resumed,'error':'Receipt or AI label unconfirmed; GET-only confirmation required.'}
        self.ledger.update(key,'published',permalink=url)
        return {**self.ledger.get(key),'publication_requests_skipped':resumed,
                'ai_verification':'official_media_get' if self.platform=='instagram' else 'explicit_container_request_and_post_receipt'}

    def confirm_image_receipt(self, key, text, image, *, resumed=True):
        """Confirm a recorded IMAGE POST receipt; this path can never issue POST."""
        import candidate_review as review
        row = self.ledger.get(key)
        review.require(self.platform == 'threads' and self.ledger.recovered and
                       row and row['state'] == 'confirming_receipt' and self.candidate_pair_id)
        saved = self.ledger.db.execute('SELECT * FROM recovery_reviews WHERE platform=? AND account_id=? AND job_id=?', key).fetchone()
        review.require(saved and saved['state'] == 'consumed' and
                       review.digest(saved['document']) == saved['document_sha256'])
        doc = json.loads(saved['document'])
        review.validate_document(doc, read_state_marker(self.ledger.directory / 'runtime.json'))
        scoped = self.ledger.db.execute('SELECT pair_id FROM pair_review_bindings WHERE review_id=?', (saved['review_id'],)).fetchone()
        approval = self.ledger.db.execute('SELECT value FROM recovery_metadata WHERE key=?', ('pair_image:'+self.candidate_pair_id,)).fetchone()
        review.require(scoped and scoped[0] == self.candidate_pair_id and approval and
                       json.loads(approval[0])['document_sha256'] == saved['document_sha256'])
        review.require(doc['delivery']['kind'] == 'IMAGE' and
                       saved['media_sha256'] == image.sha256 and
                       saved['content_hash'] == row['content_hash'] == content_hash(text, image))
        post_id = api_id(row['post_id'])
        try:
            self.check()
            receipt = api_dict(self.client.request('GET', '/' + post_id,
                              params={'fields': 'id,permalink,media_type'}))
        except ApiError:
            return {**self.ledger.get(key), 'publication_requests_skipped': resumed,
                    'error': 'Receipt confirmation unavailable; only GET confirmation may resume.'}
        url = receipt.get('permalink', '')
        if (receipt.get('id') != post_id or receipt.get('media_type') != 'IMAGE' or
                not isinstance(url, str) or not re.fullmatch(r'https://(?:www\.)?threads\.(?:com|net)/[^\s]+', url)):
            self.ledger.update(key, 'unknown')
            return {**self.ledger.get(key), 'publication_requests_skipped': resumed}
        self.ledger.update(key, 'published', permalink=url)
        return {**self.ledger.get(key), 'media_type': 'IMAGE', 'publication_requests_skipped': resumed}

    def publish(self, job_id, text, video=None, reply_to_job_id=None):
        text = validate_text(self.platform, text)
        import media_policy
        import publication_profile
        profile = publication_profile.configured()
        publication_binding = None
        if reply_to_job_id and (profile or self.ai_generated_text):
            raise SafeError('Profile and explicit text disclosure currently support root posts only.')
        receipt_policy = profile or ({'ai_disclosure': True} if self.platform in ('threads','instagram') and (video.ai_generated if video else self.ai_generated_text) else None)
        recorded_profile = self.ledger.get((self.platform,self.expected,job_id)) if receipt_policy else None
        confirming_profile = bool(recorded_profile and recorded_profile['state'] == 'confirming_label')
        if profile:
            if getattr(self.ledger, 'recovered', False):
                raise SafeError('Configured profile is not yet supported by historical recovery candidate approvals; no state was changed.')
            approved = publication_profile.bind(profile,self.publication_evidence,self.source_file,self.platform,text,video,confirming=confirming_profile)
            if job_id != approved['job_id']:
                raise SafeError('Profile capture requires its source-hash job ID; aliases cannot bypass duplicate history.')
            publication_binding = approved['binding_sha256']
        if profile and self.reviewed_hash != content_hash(text,video,ai_generated_text=self.ai_generated_text,publication_binding=publication_binding):
            raise SafeError('Configured profile requires the exact reviewed plan hash before any publication or confirmation.')
        if confirming_profile:
            if recorded_profile['content_hash'] != content_hash(text,video,ai_generated_text=self.ai_generated_text,publication_binding=publication_binding):
                raise SafeError('Receipt confirmation requires the identical approved profile and candidate.')
            return self.confirm_profile_receipt((self.platform,self.expected,job_id),video,receipt_policy)
        if profile and recorded_profile is None:
            rows = self.ledger.db.execute('SELECT state,updated_at FROM jobs WHERE platform=? AND account_id=?', (self.platform,self.expected)).fetchall()
            for prior in rows:
                if prior['state'] not in ('published','failed','duplicate'):
                    raise SafeError('Unresolved prior account job blocks a new profile publication.')
                if prior['state'] == 'published' and (prior['updated_at'] is None or self.clock()-prior['updated_at'] < profile['minimum_publish_interval_hours']*3600):
                    raise SafeError('Configured per-account publication interval has not elapsed.')
        if self.approved_source_kind:
            media_policy.require_format(self.platform, video, self.approved_source_kind)
        if type(self.ai_generated_text) is not bool or (self.ai_generated_text and (self.platform != 'threads' or video is not None)):
            raise SafeError('Explicit text disclosure is supported only for Threads text.')
        if self.platform == "instagram" and not video:
            raise SafeError("Instagram publishing in this tool requires a Reel video.")
        reply_to_id = None
        if reply_to_job_id:
            if self.platform != "threads":
                raise SafeError("Follow-ups are only supported for saved Threads root posts.")
            reply_to_id = require_root_receipt(self.ledger.get(("threads", self.expected, reply_to_job_id)))
        if isinstance(video,Image) and (self.platform != 'threads' or not self.candidate_pair_id):
            raise SafeError('Image fallback is restricted to the explicit Threads pair.')
        if video:
            video.verify_for_publish()
            if self.instagram_upload_mode and video.url is not None:
                raise SafeError("Instagram resumable mode requires a local video without --video-url.")
            if self.platform in ("threads", "instagram") and not self.instagram_upload_mode and not isinstance(video, Image):
                public_video_url(video.url)
        key = (self.platform, self.expected, job_id)
        recorded = self.ledger.get(key)
        if isinstance(video, Image) and recorded and recorded['state'] == 'confirming_receipt':
            return self.confirm_image_receipt(key, text, video)
        if getattr(self.ledger, "recovered", False) and self.ledger.has_review(key):
            checked_media = self.ledger.prepare_review(key, self.client, self.clock, video, self.instagram_upload_mode, self.candidate_pair_id)
            if isinstance(video, Image):
                video = checked_media
        elif getattr(self.ledger, "recovered", False):
            # Hold the database flock throughout fresh account-history comparison and POST.
            self.check()
            edge = f"/{self.expected}/" + ("threads" if self.platform == "threads" else "media")
            history = api_dict(self.client.request("GET", edge, params={"fields":"id,permalink,timestamp","limit":"25"}))
            if api_dict(history.get("paging", {})).get("next"):
                raise SafeError("Recovered runtime requires complete history reconciliation before publication.")
            rows = history.get("data")
            if not isinstance(rows, list):
                raise SafeError("Owned publication history is unavailable.")
            actual = [api_id(api_dict(item).get("id")) for item in rows]
            known = {row[0] for row in self.ledger.db.execute("SELECT post_id FROM jobs WHERE platform=? AND account_id=? AND state='published'",key[:2])}
            if len(actual) != len(set(actual)) or set(actual) != known:
                raise SafeError("Official history changed; reconcile before publication or retry.")
            receipts = {row["post_id"]: dict(row) for row in self.ledger.db.execute(
                "SELECT post_id,permalink,updated_at FROM jobs WHERE platform=? AND account_id=? AND state='published'", key[:2])}
            for item in rows:
                try:
                    published = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
                    if published.tzinfo is None:
                        raise ValueError()
                    published_at = published.timestamp()
                except (KeyError, TypeError, ValueError, AttributeError):
                    raise SafeError("Official publication timestamp is unavailable.") from None
                if item.get("permalink") != receipts[item["id"]]["permalink"]:
                    raise SafeError("Official publication link changed; reconcile before publication.")
                if self.clock() - published_at < 21600:
                    raise SafeError("Official account history is within the six-hour publication interval.")

        claim = self.ledger.claim if self.candidate_pair_id is None else lambda *a, **kw: self.ledger.claim_pair(self.candidate_pair_id,*a,**kw)
        row, fresh = claim(key, content_hash(text, video, reply_to_id, ai_generated_text=self.ai_generated_text, publication_binding=publication_binding), reply_to_id,
                                       self.instagram_login_mode if self.platform == "instagram" else None,
                                       self.instagram_upload_mode,
                                       **({"media_sha256": video.sha256 if video else None} if getattr(self.ledger, "recovered", False) else {}))
        resume = False
        if not fresh:
            if row["state"] == "media_processing":
                resume = self.ledger.acquire_processing(key, self.clock())
            if not resume:
                return {**self.ledger.get(key), "network_requests_skipped": not getattr(self.ledger, "recovered", False),
                        "publication_requests_skipped": True}
        try:
            self.check()
            if reply_to_id:
                parent = api_dict(self.client.request("GET", f"/{reply_to_id}", params={"fields": "id,owner"}))
                if parent.get("id") != reply_to_id or api_dict(parent.get("owner")).get("id") != self.expected:
                    raise SafeError("Follow-up parent is not this account's confirmed root post.")
            if resume:
                pending = self.poll(key)
                if pending is not None:
                    return pending
            elif self.platform in ("threads", "instagram"):
                if self.platform == "instagram":
                    payload = {"media_type": "REELS", "caption": text,
                               "is_ai_generated": str(video.ai_generated).lower()}
                else:
                    # Voluntary transparency policy for AI-written text, not a blanket Meta requirement.
                    payload = {"media_type": ("IMAGE" if isinstance(video,Image) else "VIDEO") if video else "TEXT", "text": text,
                               "is_made_with_ai": str(video.ai_generated if video else self.ai_generated_text).lower()}
                if reply_to_id:
                    payload["reply_to_id"] = reply_to_id
                if video:
                    if self.instagram_upload_mode == "resumable":
                        payload["upload_type"] = "resumable"
                    else:
                        payload["image_url" if isinstance(video,Image) else "video_url"] = video.url
                endpoint = "media" if self.platform == "instagram" else "threads"
                if self.platform == 'threads' and getattr(self.ledger, 'recovered', False) and self.ledger.has_review(key):
                    import delivery_binding
                    delivery_binding.check_handoff(self.ledger, key, video)
                result = api_dict(self.client.request("POST", f"/{self.expected}/{endpoint}", payload))
                container = api_id(result.get("id"))
                self.ledger.update(key, "uploading" if self.instagram_upload_mode else "checking_media", container_id=container,
                                   media_expires_at=self.clock() + 86400, poll_count=0)
                if self.instagram_upload_mode == "resumable":
                    # Persist before streaming. A crash or lost receipt cannot resend the file.
                    uri = instagram_upload_uri(container, result.get("uri"))
                    uploaded = api_dict(self.client.upload_instagram_video(container, uri, video))
                    if uploaded.get("success") is not True:
                        raise ApiError()
                if video:
                    return self.pending(key)
                # Retain the short text path; slow containers become resumable.
                self.wait(30)
                pending = self.poll(key)
                if pending is not None:
                    return pending
            elif video:
                pending = self.upload_x(key, video)
                if pending is not None:
                    return pending
            if self.platform in ("threads", "instagram"):
                container = api_id(self.ledger.get(key)["container_id"])
                self.ledger.update(key, "publishing")
                endpoint = "media_publish" if self.platform == "instagram" else "threads_publish"
                result = self.client.request("POST", f"/{self.expected}/{endpoint}", {"creation_id": container})
                post_id = api_id(api_dict(result).get("id"))
            else:
                payload = {"text": text}
                if video:
                    payload["media"] = {"media_ids": [api_id(self.ledger.get(key)["media_id"])]}
                    payload["made_with_ai"] = video.ai_generated
                self.ledger.update(key, "publishing")
                result = self.client.request("POST", "/tweets", payload)
                post_id = api_id(api_dict(api_dict(result).get("data")).get("id"))
            self.ledger.update(key, "confirming_label" if receipt_policy else "confirming_receipt" if isinstance(video, Image) else "published", post_id=post_id)
        except ApiError as exc:
            if resume and self.ledger.get(key)["state"] == "checking_media":
                return self.pending(key, error="Account or processing check failed; no public POST was attempted.")
            state = "failed" if exc.status and 400 <= exc.status < 500 and exc.status not in (408, 409) else "unknown"
            self.ledger.update(key, state, http_status=exc.status)
            return {**self.ledger.get(key), "error": "API request failed; no automatic retry."}
        except SafeError:
            self.ledger.update(key, "failed")
            raise
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            self.ledger.update(key, "unknown")
            return {**self.ledger.get(key), "error": "Unexpected API response; no automatic retry."}
        if receipt_policy:
            return self.confirm_profile_receipt(key,video,receipt_policy,resumed=False)
        if isinstance(video, Image):
            return self.confirm_image_receipt(key, text, video, resumed=False)
        # A missing optional permalink never erases a successful POST receipt.
        if self.platform == "x":
            self.ledger.update(key, "published", permalink=f"https://x.com/i/status/{post_id}")
        else:
            try:
                receipt = self.client.request("GET", f"/{post_id}", params={"fields": "id,permalink,media_type" if isinstance(video,Image) else "id,permalink"})
                if isinstance(video,Image) and (receipt.get('id') != post_id or receipt.get('media_type') != 'IMAGE'):
                    raise ApiError()
                url = receipt.get("permalink", "")
                pattern = r"https://(?:www\.)?instagram\.com/[^\s]+" if self.platform == "instagram" else r"https://(?:www\.)?threads\.(?:com|net)/[^\s]+"
                if receipt.get("id") == post_id and re.fullmatch(pattern, url):
                    self.ledger.update(key, "published", permalink=url)
                elif isinstance(video,Image):
                    raise ApiError()
            except (ApiError, AttributeError, TypeError):
                if isinstance(video,Image):
                    self.ledger.update(key,'unknown')
        result = self.ledger.get(key)
        if isinstance(video,Image) and result['state']=='published':
            result['media_type']='IMAGE'
        return result

    def metrics(self, job_id):
        row = self.ledger.get((self.platform, self.expected, job_id))
        if not row or row["state"] != "published" or not row["post_id"]:
            raise SafeError("Metrics require this account's published job receipt.")
        post_id = api_id(row["post_id"])
        self.check()
        if self.platform == "x":
            result = self.client.request("GET", f"/tweets/{post_id}", params={"tweet.fields": "author_id,public_metrics"})
            data = api_dict(api_dict(result).get("data"))
            if data.get("author_id") != self.expected:
                raise SafeError("Post author does not match the expected account.")
            metrics = api_dict(data.get("public_metrics"))
        else:
            receipt = self.client.request("GET", f"/{post_id}", params={"fields": "owner"})
            if api_dict(api_dict(receipt).get("owner")).get("id") != self.expected:
                raise SafeError("Post author does not match the expected account.")
            names = ("views", "reach", "likes", "comments", "saved", "shares") if self.platform == "instagram" else ("views", "likes", "replies", "reposts", "quotes", "shares")
            result = self.client.request("GET", f"/{post_id}/insights", params={"metric": ",".join(names)})
            data = api_dict(result).get("data")
            if not isinstance(data, list):
                raise ApiError()
            metrics = {}
            for item in data:
                item = api_dict(item)
                values = item.get("values", [])
                if not isinstance(values, list) or item.get("name") not in names:
                    raise ApiError()
                metrics[item["name"]] = (api_dict(values[0]).get("value") if values else
                                         api_dict(item["total_value"]).get("value") if "total_value" in item else None)
        if any(value is not None and not isinstance(value, (int, float)) for value in metrics.values()):
            raise ApiError()
        return {"platform": self.platform, "account_id": self.expected, "post_id": post_id,
                "metrics": metrics, "collected_at": time.time()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "check", "publish", "metrics"))
    parser.add_argument("--platform", required=True, choices=BASES)
    parser.add_argument("--instagram-login-mode", choices=INSTAGRAM_BASES,
                        help="Required for Instagram: explicitly choose the app's Instagram Login or Unified Login setup.")
    parser.add_argument("--instagram-upload-mode", choices=("resumable",),
                        help="Explicit Instagram Unified local-video upload; requires --video-file and forbids --video-url.")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--text-file")
    parser.add_argument("--image-file", help="Exact reviewed JPEG for the one-pair Threads image fallback.")
    parser.add_argument("--image-url", help="Legacy image URL input; ignored for delivery, which resolves the approved official object afresh.")
    parser.add_argument("--video-file", help="Final local MP4 for video preflight.")
    parser.add_argument("--video-url", help="Threads/Instagram: public HTTPS URL serving the same immutable MP4.")
    parser.add_argument("--reviewed-hash", help="Exact plan content_sha256 required when a publication profile is configured.")
    parser.add_argument("--publication-evidence", help="Private exact source/language/capture review for the configured profile.")
    parser.add_argument("--source-file", help="Private approved capture original for profile source hash verification.")
    parser.add_argument("--approved-source-kind", choices=("video", "screenshot", "text"), help="Approved source inventory; delivery failure must not change it.")
    parser.add_argument("--ai-generated-text", action="store_true", help="Explicitly approved AI-generated/ substantially transformed Threads text disclosure.")
    parser.add_argument("--ai-generated-media", action="store_true", help="Disclose actual AI-generated media; ordinary product captures or scripted Blender renders do not imply this flag.")
    parser.add_argument("--video-kind", choices=("real-demo", "blender-render", "ai-stylized", "ai-realistic"), default="real-demo")
    parser.add_argument("--audio-kind", choices=("original", "none", "ai-stylized", "ai-realistic"), default="original")
    parser.add_argument("--reply-to-job-id", help="Threads only: saved, published root job in this account's ledger. No arbitrary post IDs.")
    parser.add_argument("--candidate-pair-id", help="Explicit one-pair admission; never enables scheduled operation.")
    parser.add_argument("--job-id")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    ledger = None
    try:
        state_path(args.state_dir)
        if args.candidate_pair_id and args.command not in ('plan','publish'):
            raise SafeError('Candidate pair ID is only valid for explicit plan or publish.')
        if args.platform == "instagram" and not args.instagram_login_mode:
            raise SafeError("Instagram requires --instagram-login-mode unified or instagram; the host is never inferred from a token.")
        if args.platform != "instagram" and args.instagram_login_mode:
            raise SafeError("--instagram-login-mode is only valid with --platform instagram.")
        if args.instagram_upload_mode and (args.platform != "instagram" or args.instagram_login_mode != "unified" or
                                           args.command not in ("plan", "publish") or not args.video_file or args.video_url):
            raise SafeError("--instagram-upload-mode resumable requires Instagram Unified plan/publish, --video-file and no --video-url.")
        prefix = args.platform.upper()
        expected = os.environ.get(prefix + "_EXPECTED_USER_ID", "")
        if not re.fullmatch(r"[0-9]+", expected) and not (args.command == "plan" and not expected):
            raise SafeError(prefix + "_EXPECTED_USER_ID is required and must be a numeric account ID.")
        if args.command != "check" and (not args.job_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", args.job_id)):
            raise SafeError("--job-id is required (1-96 letters, numbers, dots, underscores or hyphens).")
        if args.reply_to_job_id and (args.platform != "threads" or args.command not in ("plan", "publish") or
                                    not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", args.reply_to_job_id)):
            raise SafeError("--reply-to-job-id is only accepted for Threads plan/publish with a valid saved job ID.")
        import publication_profile
        profile = publication_profile.configured() if args.command in ("plan", "publish") else None
        publication_binding = None
        if args.command in ("plan", "publish"):
            if not args.text_file:
                raise SafeError("--text-file is required.")
            text = validate_text(args.platform, Path(args.text_file).read_text(encoding="utf-8"))
            if args.image_file or args.image_url:
                if (not args.image_file or args.platform != 'threads' or not args.candidate_pair_id or args.video_file or args.video_url or args.ai_generated_media or args.reply_to_job_id):
                    raise SafeError('Image fallback requires only its exact Threads pair JPEG.')
                video = prepare_image(args.image_file,args.image_url,allow_missing_url=True)
            else:
                video = prepare_video(args.platform, args.video_file, args.video_url, profile["ai_disclosure"] if profile else args.ai_generated_media,
                                      args.video_kind, args.audio_kind,
                                      allow_missing_url=args.command == "plan" or args.instagram_upload_mode == "resumable")
            if profile:
                if args.ai_generated_media and not profile['ai_disclosure']:
                    raise SafeError('Explicit disclosure conflicts with configured profile.')
                approved = publication_profile.bind(profile,args.publication_evidence,args.source_file,args.platform,text,video,confirming=args.command=='publish')
                if args.job_id != approved['job_id']:
                    raise SafeError('Profile capture requires job ID capture- followed by the full approved source SHA256.')
                publication_binding = approved['binding_sha256']
            import media_policy
            source_kind = args.approved_source_kind or ('screenshot' if isinstance(video, Image) else 'video' if video else 'text')
            media_policy.require_format(args.platform, video, source_kind)
            if args.ai_generated_text and (args.platform != 'threads' or video is not None):
                raise SafeError('Explicit text disclosure is supported only for Threads text.')
            if args.platform == "instagram" and not video:
                raise SafeError("Instagram publishing in this tool requires a Reel video.")
        elif args.publication_evidence or args.source_file or args.approved_source_kind or args.ai_generated_text or args.image_file or args.image_url or args.video_file or args.video_url or args.ai_generated_media or args.video_kind != "real-demo" or args.audio_kind != "original":
            raise SafeError("Video options are only supported by plan and publish.")
        if args.command == "plan":
            reply_to_id = (read_reply_parent(args.state_dir, expected, args.reply_to_job_id)
                           if args.reply_to_job_id and expected else None)
            token_configured = bool(os.environ.get(prefix + "_USER_ACCESS_TOKEN", "")) and not any(
                c.isspace() for c in os.environ.get(prefix + "_USER_ACCESS_TOKEN", ""))
            blockers = []
            if not expected:
                blockers.append("account_unconfigured")
            if not token_configured:
                blockers.append("token_unconfigured")
            if video and args.platform in ("threads", "instagram") and not video.url and not args.instagram_upload_mode:
                blockers.append("image_url_missing" if isinstance(video,Image) else "video_url_missing")
            if video and video.preflight != "verified_local_profile":
                blockers.append("local_video_unverified")
            if args.reply_to_job_id and not reply_to_id:
                blockers.append("reply_parent_unverified")
            ai_field = "is_ai_generated" if args.platform == "instagram" else "is_made_with_ai" if args.platform == "threads" else "made_with_ai"
            result = {"platform": args.platform, "account_id": expected or None, "job_id": args.job_id,
                      "auth_mode": args.instagram_login_mode,
                      "upload_mode": args.instagram_upload_mode,
                      "preview": text, "unicode_codepoints": len(text), "utf8_bytes": len(text.encode()),
                      "content_sha256": (None if args.reply_to_job_id and not reply_to_id else content_hash(text, video, reply_to_id, ai_generated_text=args.ai_generated_text, publication_binding=publication_binding)),
                      "network_requests": 0, "state_writes": 0, "publish_ready": False,
                      "readiness": {"status": "NOT_READY" if blockers else "UNVERIFIED", "reasons": blockers,
                                    "account_live_verified": False,
                                    "note": "Offline review only. Exit 0 means local review completed, not account authentication, persistent-state validation, disclosure approval or publication readiness."},
                      "reply_to_job_id": args.reply_to_job_id, "reply_to_id": reply_to_id,
                      "parent_ownership_live_verified": False if reply_to_id else None,
                      "ai_label": {"field": ai_field, "value": video.ai_generated if video else args.ai_generated_text,
                                   "note": "Disclosure follows explicit source/user findings; ordinary capture or AI coding alone does not imply generated media."},
                      "media": video.summary() if video else None,
                      "estimated_publish_request_cost_usd": 0.015 if args.platform == "x" else None,
                      "cost_note": "X post-write estimate only; account checks, reads, media upload and status requests are not included. Threads has no published separate API fee. Not a live quote.",
                      "limits_note": "Tool limits: X <=140 codepoints, no link-like fragments; Threads <=500 UTF-8 bytes; Instagram <=2200 caption codepoints and Reel <=300 MB. Sensitive-pattern checks are incomplete."}
        else:
            if args.command == "publish" and not args.execute:
                raise SafeError("Publishing requires --execute after reviewing the plan.")
            token = os.environ.get(prefix + "_USER_ACCESS_TOKEN", "")
            if not token or any(c.isspace() for c in token):
                raise SafeError(prefix + "_USER_ACCESS_TOKEN is required and must not contain whitespace.")
            if args.command != "check":
                require_state_binding(args.state_dir, args.platform, expected, args.instagram_login_mode)
                ledger = Ledger(args.state_dir)
            publisher = Publisher(args.platform, expected, APIClient(args.platform, token, args.instagram_login_mode), ledger,
                                  instagram_login_mode=args.instagram_login_mode, instagram_upload_mode=args.instagram_upload_mode,
                                  **({"candidate_pair_id": args.candidate_pair_id} if args.candidate_pair_id else {}),
                                  **({"ai_generated_text": True} if args.ai_generated_text else {}),
                                  **({"approved_source_kind": args.approved_source_kind} if args.approved_source_kind else {}),
                                  **({"publication_evidence": args.publication_evidence, "source_file": args.source_file, "reviewed_hash": args.reviewed_hash} if profile else {}))
            result = (publisher.check() if args.command == "check" else
                      publisher.publish(args.job_id, text, video, args.reply_to_job_id) if args.command == "publish" else publisher.metrics(args.job_id))
        if args.command == 'plan':
            result['publication_profile'] = {'profile_id': profile['profile_id'], 'binding_sha256': publication_binding, 'check_interval_hours': profile['check_interval_hours'], 'minimum_publish_interval_hours': profile['minimum_publish_interval_hours']} if profile else None
            result['approved_source_kind'] = source_kind
            result['source_formats'] = media_policy.formats(approved_video=source_kind=='video', approved_screenshot=source_kind=='screenshot')
        if args.command == 'plan' and args.candidate_pair_id:
            result['candidate_pair_id'] = args.candidate_pair_id
            result['pair_live_admission_checked'] = False
        print(json.dumps(result, ensure_ascii=False))
        if result.get("state") in ("media_processing", "confirming_label", "confirming_receipt"):
            return 3
        return 1 if result.get("state") in ("unknown", "failed", "claimed", "container", "publishing", "duplicate", "uploading", "checking_media", "processing_stalled") else 0
    except SafeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    except ApiError as exc:
        print(json.dumps({"error": "API request failed.", "http_status": exc.status}))
        return 1
    except (OSError, UnicodeError, sqlite3.Error):
        print(json.dumps({"error": "Local file or ledger operation failed. No automatic retry."}))
        return 2
    except (AttributeError, KeyError, TypeError, ValueError, IndexError):
        print(json.dumps({"error": "Unexpected API response. No automatic retry."}))
        return 1
    finally:
        if ledger:
            ledger.close()


if __name__ == "__main__":
    # Shared helpers must use this executor and its exception classes, not load a second copy.
    sys.modules["publisher"] = sys.modules[__name__]
    raise SystemExit(main())
