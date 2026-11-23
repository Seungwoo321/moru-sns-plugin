#!/usr/bin/env python3
"""Verify configured accounts and existing private state; initialize only a wholly new state directory."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.parse

import private_runtime as runtime
import publisher as p


check_node = p.check_state_node
identity = p.file_identity
read_marker = p.read_state_marker
inspect_state = p.inspect_state


def state_location(value, initialize):
    # private_path rejects every original symlink component before resolving,
    # traversal, Git/skills paths, and any parent not owned 0700 by this user.
    path = runtime.private_path(value)
    if initialize:
        if path.exists():
            raise p.SafeError("Initialization requires a wholly new state directory.")
    else:
        check_node(path, directory=True)  # Missing state never creates a replacement.
    return path


def initialize_state(path, runtime_id, bindings):
    path = state_location(str(path), initialize=True)
    parent = runtime.parent_fd(path)
    directory = None
    try:
        # mkdir is exclusive: even an existing empty directory must be reviewed,
        # not adopted or silently used to rebuild lost publication history.
        os.mkdir(path.name, mode=0o700, dir_fd=parent)
        directory = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        if identity(os.fstat(directory)) != identity(check_node(path, directory=True)):
            raise p.SafeError("State directory changed during initialization.")
        ledger = p.Ledger(str(path), initialize=True)
        ledger.close()
        info = check_node(path / "publisher.sqlite3")
        fd = os.open("publisher.sqlite3", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        marker = {"version": 1, "runtime_id": runtime_id, "accounts": bindings,
                  "ledger_identity": identity(info), "created_at": int(time.time())}
        fd = os.open("runtime.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(marker, target, ensure_ascii=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.fsync(directory)
        os.fsync(parent)
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def verify_ffprobe():
    binary = shutil.which("ffprobe")
    if not binary:
        raise p.SafeError("ffprobe is unavailable.")
    result = subprocess.run([binary, "-version"], capture_output=True, text=True, timeout=5, check=False)
    if result.returncode != 0 or not result.stdout.startswith("ffprobe version "):
        raise p.SafeError("ffprobe could not be verified.")


def sanitized_posts(rows, platform):
    if not isinstance(rows, list) or len(rows) > 25:
        raise p.SafeError("Owned media response is invalid.")
    hosts = {"threads.com", "www.threads.com", "threads.net", "www.threads.net"} if platform == "threads" else {"instagram.com", "www.instagram.com"}
    posts = []
    for item in rows:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[0-9]{1,40}", item["id"]):
            raise p.SafeError("Owned media ID is invalid.")
        link, timestamp = item.get("permalink"), item.get("timestamp")
        if link is not None:
            if not isinstance(link, str) or len(link) > 2048 or any(c.isspace() or ord(c) < 32 for c in link):
                raise p.SafeError("Owned media link is invalid.")
            parsed = urllib.parse.urlsplit(link)
            if (parsed.scheme != "https" or parsed.hostname not in hosts or parsed.username or parsed.password or
                    parsed.port not in (None, 443) or parsed.query or parsed.fragment or "\\" in link):
                raise p.SafeError("Owned media link is invalid.")
        if timestamp is not None and (not isinstance(timestamp, str) or not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:?[0-9]{2})", timestamp)):
            raise p.SafeError("Owned media timestamp is invalid.")
        posts.append({"id": item["id"], "permalink": link, "timestamp": timestamp})
    return posts


def run(args):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.runtime_id):
        raise p.SafeError("Runtime ID is invalid.")
    path = state_location(args.state_dir, args.initialize)
    configs, bindings = {}, {}
    connections = args.connection or []
    if len(connections) != len(set(connections)):
        raise p.SafeError("Duplicate environment connection.")
    for platform, filename in (("threads", args.threads_config), ("instagram", args.instagram_config), ("x", args.x_config)):
        if filename and platform in connections:
            raise p.SafeError("Choose one credential source per platform.")
        if not filename and platform not in connections:
            continue
        config = runtime.load_config(filename) if filename else runtime.load_environment(platform)
        if config["platform"] != platform:
            raise p.SafeError("Config platform does not match.")
        configs[platform] = config
        bindings[platform] = {"account_id": config["expected_user_id"], "username": config["expected_username"],
                              "auth_mode": config["instagram_login_mode"]}
    if not configs:
        raise p.SafeError("At least one platform config is required.")
    jobs = None if args.initialize else inspect_state(path, args.runtime_id, bindings)
    need_video = args.require_video or "instagram" in configs
    if need_video:
        verify_ffprobe()
    accounts = {}
    for platform, config in configs.items():
        account = runtime.verify_identity(platform, config["access_token"], config["expected_username"],
                                          config["expected_user_id"], config["instagram_login_mode"])
        if platform == "x":
            accounts[platform] = {**bindings[platform], "account_matches_config": True,
                                  "recent_owned_posts": None, "owned_post_list_checked": False}
            continue
        client = p.APIClient(platform, config["access_token"], config["instagram_login_mode"])
        edge = f"/{account}/" + ("threads" if platform == "threads" else "media")
        result = client.request("GET", edge, params={"fields": "id,permalink,timestamp", "limit": "25"})
        accounts[platform] = {**bindings[platform], "account_matches_config": True,
                              "recent_owned_posts": sanitized_posts(p.api_dict(result).get("data"), platform),
                              "owned_post_list_checked": True}
    if args.initialize:
        initialize_state(path, args.runtime_id, bindings)
        jobs = inspect_state(path, args.runtime_id, bindings)
    output = {"status": "checked", "checked_at": int(time.time()), "initialized": args.initialize,
              "runtime_id": args.runtime_id, "state_integrity": "ok", "state_marker_matches": True,
              "ledger_jobs": jobs, "ffprobe_verified": need_video, "accounts": accounts,
              "post_list_scope": "threads_instagram_first_page_max_25_x_not_queried", "persistence_guaranteed": False,
              "publishing_and_insights_verified": False, "previous_token_revocation_verified": False}
    rendered = json.dumps(output, ensure_ascii=True)
    for config in configs.values():
        rendered = runtime.redact(rendered, config["access_token"])
    print(rendered)
    return 0


def main(argv=None):
    parser = runtime.PrivateArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("state-dir", "runtime-id"):
        parser.add_argument("--" + name, required=True)
    for name in ("threads-config", "instagram-config", "x-config"):
        parser.add_argument("--" + name)
    parser.add_argument("--connection", choices=p.BASES, action="append")
    parser.add_argument("--require-video", action="store_true", help="Verify ffprobe for Threads/X video; always required for Instagram.")
    parser.add_argument("--initialize", action="store_true")
    args = parser.parse_args(argv)
    previous_umask = os.umask(0o077)
    try:
        return run(args)
    except Exception:
        print(json.dumps({"status": "blocked", "error": "Runtime verification failed; do not initialize or publish to bypass this result."}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"status": "blocked", "error": "Runtime verification cancelled."}))
        return 130
    finally:
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
