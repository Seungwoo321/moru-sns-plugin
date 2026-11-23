#!/usr/bin/env python3
"""Configure one private account token with hidden input; run the existing publisher."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time
import urllib.parse
import warnings

import publisher as p


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid arguments. Secret values are not accepted on the command line.\n")


def secret_variants(token):
    return {token, urllib.parse.quote(token, safe=""), urllib.parse.quote_plus(token)}


def redact(value, token):
    for secret in secret_variants(token):
        value = value.replace(secret, "[REDACTED]")
    return value


def private_path(value, create=False):
    path = p.state_path(value)
    if create:
        missing, parent = [], path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
    info = path.parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise p.SafeError("The config parent must be owned by this user with mode 0700.")
    return path


def parent_fd(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        os.close(fd)
        raise p.SafeError("The private config parent changed or is not protected.")
    return fd


def store_config(path, data):
    """Publish a complete 0600 file atomically, refusing any existing destination."""
    path = private_path(str(path), create=True)
    directory = parent_fd(path)
    temporary = ".sns-config-" + secrets.token_hex(12)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(data, target, ensure_ascii=False)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        # A hard link makes the complete file visible atomically without replacing
        # an existing file or following a destination symlink.
        os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        os.close(directory)


def valid_token(token):
    return isinstance(token, str) and 8 <= len(token) <= 32768 and not any(c.isspace() or ord(c) < 32 for c in token)


def valid_username(username):
    return isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_.]{1,64}", username)


def inspect_environment(platform):
    """Inspect injected values in memory; diagnostics contain key names only."""
    prefix = platform.upper()
    validators = {
        prefix + "_USER_ACCESS_TOKEN": valid_token,
        prefix + "_EXPECTED_USER_ID": lambda v: bool(re.fullmatch(r"[0-9]+", v)),
        prefix + "_EXPECTED_USERNAME": valid_username,
    }
    if platform == "instagram":
        validators["INSTAGRAM_LOGIN_MODE"] = lambda v: v in p.INSTAGRAM_BASES
    missing, invalid = [], []
    for key, validate in validators.items():
        value = os.environ.get(key, "")
        if not value:
            missing.append(key)
        elif not validate(value):
            invalid.append(key)
    return {"source": "environment", "connection": platform,
            "status": "blocked" if missing or invalid else "ready_for_account_check",
            "missing": missing, "invalid": invalid, "network_requests": 0}


def load_environment(platform):
    diagnosis = inspect_environment(platform)
    if diagnosis["status"] != "ready_for_account_check":
        raise p.SafeError("Environment connection is incomplete or invalid; run doctor for key names.")
    prefix = platform.upper()
    return {"version": 1, "platform": platform,
            "access_token": os.environ[prefix + "_USER_ACCESS_TOKEN"],
            "expected_user_id": os.environ[prefix + "_EXPECTED_USER_ID"],
            "expected_username": os.environ[prefix + "_EXPECTED_USERNAME"],
            "instagram_login_mode": os.environ["INSTAGRAM_LOGIN_MODE"] if platform == "instagram" else None,
            "expiry_status": "unknown", "expires_at": None}


def doctor(args):
    diagnosis = inspect_environment(args.connection)
    print(json.dumps(diagnosis))
    return 0 if diagnosis["status"] == "ready_for_account_check" else 2


def load_config(value):
    path = private_path(value)
    directory = parent_fd(path)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 65536):
            raise p.SafeError("Config must be a private, regular, single-link 0600 file owned by this user.")
        data = json.load(source)
    if (not isinstance(data, dict) or data.get("version") != 1 or data.get("platform") not in p.BASES or
            not valid_token(data.get("access_token")) or not valid_username(data.get("expected_username")) or
            not isinstance(data.get("expected_user_id"), str) or not re.fullmatch(r"[0-9]+", data["expected_user_id"]) or
            data.get("expiry_status") != "unknown" or data.get("expires_at") is not None):
        raise p.SafeError("Private config is invalid; no publisher command was run.")
    mode = data.get("instagram_login_mode")
    if (data["platform"] == "instagram" and mode not in p.INSTAGRAM_BASES) or (data["platform"] != "instagram" and mode is not None):
        raise p.SafeError("Private config login mode is invalid.")
    return data


def verify_identity(platform, token, username, expected_id=None, mode=None):
    client = p.APIClient(platform, token, mode)
    if platform == "instagram" and mode == "unified":
        if not expected_id:
            raise p.SafeError("Unified Login requires the caller's numeric expected account ID.")
        params, seen = {"fields": "id,username"}, set()
        for _ in range(10):
            result = p.api_dict(client.request("GET", "/me/assigned_instagram_accounts", params=params))
            accounts = result.get("data")
            if not isinstance(accounts, list):
                raise p.ApiError()
            for account in accounts:
                account = p.api_dict(account)
                if p.api_id(account.get("id")) == expected_id:
                    if account.get("username") != username:
                        raise p.SafeError("Verified account username does not match the caller's expected username.")
                    return expected_id
            after = p.api_dict(p.api_dict(result.get("paging", {})).get("cursors", {})).get("after")
            if not after:
                break
            if not isinstance(after, str) or len(after) > 4096 or after in seen or not re.fullmatch(r"[A-Za-z0-9_+/=-]+", after):
                raise p.SafeError("Assigned-account pagination is invalid or repeated.")
            seen.add(after)
            params = {"fields": "id,username", "after": after}
        raise p.SafeError("The expected Instagram account was not confirmed.")
    path = "/users/me" if platform == "x" else "/me"
    params = None if platform == "x" else {"fields": "user_id,username" if platform == "instagram" else "id,username"}
    result = p.api_dict(client.request("GET", path, params=params))
    profile = p.api_dict(result.get("data")) if platform == "x" else result
    if platform == "instagram" and "data" in result:
        data = result["data"]
        if not isinstance(data, list) or len(data) != 1:
            raise p.ApiError()
        profile = p.api_dict(data[0])
    identity = p.api_id(profile.get("user_id" if platform == "instagram" else "id"))
    if profile.get("username") != username or (expected_id and identity != expected_id):
        raise p.SafeError("Verified account does not match the caller's expected username/account ID.")
    return identity


def configure(args):
    if not valid_username(args.expected_username):
        raise p.SafeError("Provide the exact expected public username without @.")
    if args.expected_user_id and not re.fullmatch(r"[0-9]+", args.expected_user_id):
        raise p.SafeError("Expected account ID must be numeric.")
    if args.platform == "instagram" and not args.instagram_login_mode:
        raise p.SafeError("Instagram requires an explicit login mode.")
    if args.platform != "instagram" and args.instagram_login_mode:
        raise p.SafeError("Instagram login mode is only valid for Instagram.")
    if args.instagram_login_mode == "unified" and not args.expected_user_id:
        raise p.SafeError("Unified Login requires the caller's numeric expected account ID.")
    path = private_path(args.config, create=True)
    if path.exists():
        raise p.SafeError("Config already exists; this initial setup helper does not overwrite credentials.")
    if not sys.stdin.isatty():
        raise p.SafeError("Hidden token entry requires an interactive terminal; stdin/token arguments are not accepted.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        token = getpass.getpass("Access token (hidden): ")
    if not valid_token(token):
        raise p.SafeError("Token input is empty or invalid.")
    identity = verify_identity(args.platform, token, args.expected_username, args.expected_user_id, args.instagram_login_mode)
    store_config(path, {"version": 1, "platform": args.platform, "expected_username": args.expected_username,
                        "expected_user_id": identity, "instagram_login_mode": args.instagram_login_mode,
                        "access_token": token, "identity_verified_at": time.time(),
                        "expiry_status": "unknown", "expires_at": None})
    print(redact(json.dumps({"status": "configured", "platform": args.platform, "account_id": identity,
                             "expected_username": args.expected_username, "identity_verified": True,
                             "expiry_status": "unknown", "persistence_verified": False}), token))
    return 0


def run(args):
    data = load_config(args.config) if args.config else load_environment(args.connection or "threads")
    if args.publisher_command in ("publish", "metrics"):
        # Fail locally before a child process or network operation can start.
        p.require_state_binding(args.state_dir, data["platform"], data["expected_user_id"],
                                data["instagram_login_mode"])
    command = [sys.executable, "-I", str(Path(__file__).with_name("publisher.py")), args.publisher_command,
               "--platform", data["platform"], "--state-dir", args.state_dir]
    if data["instagram_login_mode"]:
        command += ["--instagram-login-mode", data["instagram_login_mode"]]
    for name in ("text_file", "video_file", "video_url", "video_kind", "audio_kind", "reply_to_job_id", "job_id", "instagram_upload_mode"):
        value = getattr(args, name)
        if value is not None:
            command += ["--" + name.replace("_", "-"), value]
    for name in ("ai_generated_media", "execute"):
        if getattr(args, name):
            command.append("--" + name.replace("_", "-"))
    env = dict(os.environ)
    for platform in p.BASES:
        env.pop(platform.upper() + "_USER_ACCESS_TOKEN", None)
        env.pop(platform.upper() + "_EXPECTED_USER_ID", None)
    prefix, token = data["platform"].upper(), data["access_token"]
    if any(secret in argument for secret in secret_variants(token) for argument in command):
        raise p.SafeError("Publisher arguments contain credential material; nothing was executed.")
    if args.publisher_command == "check":
        identity = verify_identity(data["platform"], data["access_token"], data["expected_username"],
                                   data["expected_user_id"], data["instagram_login_mode"])
        print(redact(json.dumps({"platform": data["platform"], "account_id": identity,
                                 "username": data["expected_username"], "account_matches": True,
                                 "auth_mode": data["instagram_login_mode"]}), data["access_token"]))
        return 0
    env[prefix + "_USER_ACCESS_TOKEN"] = token
    env[prefix + "_EXPECTED_USER_ID"] = data["expected_user_id"]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)
    for value, stream in ((result.stdout, sys.stdout), (result.stderr, sys.stderr)):
        stream.write(redact(value, token))
    return result.returncode


def main(argv=None):
    parser = PrivateArgumentParser(description=__doc__, allow_abbrev=False)
    sub = parser.add_subparsers(dest="action", required=True)
    diagnose = sub.add_parser("doctor", allow_abbrev=False)
    diagnose.add_argument("--connection", choices=p.BASES, default="threads")
    setup = sub.add_parser("configure", allow_abbrev=False)
    setup.add_argument("--config", required=True)
    setup.add_argument("--platform", choices=p.BASES, required=True)
    setup.add_argument("--expected-username", required=True)
    setup.add_argument("--expected-user-id")
    setup.add_argument("--instagram-login-mode", choices=p.INSTAGRAM_BASES)
    execute = sub.add_parser("run", allow_abbrev=False)
    execute.add_argument("publisher_command", choices=("check", "plan", "publish", "metrics"))
    source = execute.add_mutually_exclusive_group()
    source.add_argument("--config")
    source.add_argument("--connection", choices=p.BASES)
    execute.add_argument("--state-dir", required=True)
    for name in ("text-file", "video-file", "video-url", "reply-to-job-id", "job-id"):
        execute.add_argument("--" + name)
    execute.add_argument("--video-kind", choices=("real-demo", "blender-render", "ai-stylized", "ai-realistic"))
    execute.add_argument("--audio-kind", choices=("original", "none", "ai-stylized", "ai-realistic"))
    execute.add_argument("--instagram-upload-mode", choices=("resumable",))
    execute.add_argument("--ai-generated-media", action="store_true")
    execute.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        return {"configure": configure, "doctor": doctor, "run": run}[args.action](args)
    except p.SafeError as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    except p.ApiError as exc:
        print(json.dumps({"error": "Official account verification failed; no credentials were saved.", "http_status": exc.status}))
        return 1
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, EOFError, getpass.GetPassWarning, subprocess.SubprocessError):
        print(json.dumps({"error": "Private runtime setup or execution failed; no secret details are logged."}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "Cancelled; no secret details are logged."}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
