#!/usr/bin/env python3
"""Check Threads refresh eligibility; --execute refreshes and atomically stores a verified token."""
import contextlib
import fcntl
import hashlib
import json
import os
import secrets
import stat
import time

import private_runtime as runtime
import publisher as p
import token_status

DAY = 86400
REFRESH_BASE = "https://graph.threads.com"


@contextlib.contextmanager
def locked_config(value):
    path = runtime.private_path(value)
    directory = runtime.parent_fd(path)
    lock = None
    try:
        name = "." + path.name + ".refresh.lock"
        lock = os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096):
            raise p.SafeError("Unsafe refresh lock.")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise p.SafeError("Refresh lock changed.")
        yield path, directory, lock
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)


def read_marker(lock):
    os.lseek(lock, 0, os.SEEK_SET)
    raw = os.read(lock, 4097)
    if not raw:
        return {}
    marker = json.loads(raw)
    if not isinstance(marker, dict):
        raise p.SafeError("Refresh marker is invalid.")
    return marker


def write_marker(lock, marker):
    raw = json.dumps(marker, separators=(",", ":")).encode()
    os.lseek(lock, 0, os.SEEK_SET)
    os.ftruncate(lock, 0)
    with os.fdopen(os.dup(lock), "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())


def eligibility(metadata, config, now):
    if metadata["is_valid"] is not True:
        return "invalid"
    issued, expires = metadata["issued_at"], metadata["expires_at"]
    if type(expires) is not int or expires <= 0:
        return "expiry_unknown"
    if expires <= now:
        return "expired"
    if issued > now or issued >= expires or expires - issued <= DAY:
        return "long_lived_timing_unconfirmed"
    if "threads_basic" not in (metadata["scopes"] or []):
        return "basic_permission_unconfirmed"
    data_expiry = metadata["data_access_expires_at"]
    if data_expiry is not None and data_expiry > 0 and data_expiry <= now:
        return "data_access_expired"
    previous = config.get("threads_refresh", {})
    if not isinstance(previous, dict):
        return "refresh_history_invalid"
    refreshed = previous.get("last_refreshed_at", 0)
    if type(refreshed) is not int or refreshed < 0 or refreshed > now:
        return "refresh_history_invalid"
    if now - max(issued, refreshed) < DAY:
        return "too_young"
    if expires - now > 7 * DAY:
        return "not_due"
    return "eligible"


def replace_config(path, directory, original, updated):
    temporary = ".threads-refresh-" + secrets.token_hex(12)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), 0o600)
            json.dump(updated, target, ensure_ascii=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        actual_parent = os.stat(path.parent, follow_symlinks=False)
        pinned_parent = os.fstat(directory)
        if (actual_parent.st_dev, actual_parent.st_ino) != (pinned_parent.st_dev, pinned_parent.st_ino):
            raise p.SafeError("Private config parent changed.")
        if runtime.load_config(str(path)) != original:
            raise p.SafeError("Private config changed during refresh.")
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def run(args):
    with locked_config(args.config) as (path, directory, lock):
        config = runtime.load_config(str(path))
        if config["platform"] != "threads":
            raise p.SafeError("Threads only.")
        client = p.APIClient("threads", config["access_token"])
        metadata = token_status.inspect_token(client, config, include_timing=True)
        now = int(time.time())
        reason = eligibility(metadata, config, now)
        fingerprint = hashlib.sha256(config["access_token"].encode()).hexdigest()
        marker = read_marker(lock)
        if marker.get("state") == "attempting" and marker.get("token_sha256") == fingerprint:
            print(json.dumps({"status": "blocked", "reason": "previous_refresh_outcome_unresolved"}))
            return 2
        if reason != "eligible" or not args.execute:
            print(json.dumps({"status": reason, "refresh_executed": False,
                              "issued_at": metadata["issued_at"], "expires_at": metadata["expires_at"]}))
            return 0 if reason in ("eligible", "not_due", "too_young") else 2
        # A persistent, secret-free marker prevents replay after an uncertain
        # network result or crash. Never remove the lock file while running.
        write_marker(lock, {"state": "attempting", "token_sha256": fingerprint, "started_at": now})
        os.fsync(directory)  # Persist a newly created lock name before the remote mutation.
        client.base = REFRESH_BASE  # The documented refresh endpoint has no /v1.0 prefix.
        result = p.api_dict(client.request("GET", "/refresh_access_token", params={"grant_type": "th_refresh_token"}))
        token, duration = result.get("access_token"), result.get("expires_in")
        if (not runtime.valid_token(token) or result.get("token_type", "").lower() != "bearer" or
                type(duration) is not int or not DAY < duration <= 60 * DAY):
            raise p.SafeError("Refresh response was invalid.")
        runtime.verify_identity("threads", token, config["expected_username"], config["expected_user_id"])
        verified_at = int(time.time())
        updated = {**config, "access_token": token, "identity_verified_at": verified_at,
                   "threads_refresh": {"last_refreshed_at": verified_at, "expires_at": now + duration,
                                       "expires_in": duration, "source": "official_refresh_response"}}
        # Keep legacy expiry_status=unknown/expires_at=null for loader compatibility.
        # Every future eligibility check obtains fresh official debug metadata.
        replace_config(path, directory, config, updated)
        write_marker(lock, {"state": "saved", "verified_at": verified_at})
        print(json.dumps({"status": "refreshed", "identity_verified": True,
                          "refreshed_at": verified_at, "expires_at": now + duration}))
        return 0


def main(argv=None):
    parser = runtime.PrivateArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception:
        print(json.dumps({"error": "Refresh was not confirmed; inspect token status before resolving or retrying."}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "Refresh interrupted; inspect token status before resolving or retrying."}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
