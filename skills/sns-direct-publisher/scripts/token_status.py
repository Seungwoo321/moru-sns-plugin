#!/usr/bin/env python3
"""Read Threads token metadata without exposing credentials or changing configs."""
import json
import re

import private_runtime as runtime
import publisher as p


FIELDS = ("is_valid", "app_id", "user_id", "expires_at", "data_access_expires_at", "scopes")


def inspect_token(client, config, *, include_timing=False):
    result = client.request("GET", "/debug_token", params={"input_token": config["access_token"]})
    data = p.api_dict(p.api_dict(result).get("data"))
    if type(data.get("is_valid")) is not bool:
        raise p.SafeError("Token metadata was incomplete or malformed; validity is unknown.")
    selected = {key: data.get(key) for key in FIELDS}
    for key in ("app_id", "user_id"):
        value = selected[key]
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,40}", value):
                raise p.SafeError("Token metadata was malformed; validity is unknown.")
    if selected["user_id"] is not None and selected["user_id"] != config["expected_user_id"]:
        raise p.SafeError("Token metadata did not match the configured account.")
    for key in ("expires_at", "data_access_expires_at"):
        value = selected[key]
        if value is not None and (type(value) is not int or value < 0):
            raise p.SafeError("Token expiry metadata was malformed; expiry is unknown.")
    scopes = selected["scopes"]
    if scopes is not None and (not isinstance(scopes, list) or len(scopes) > 100 or
            any(not isinstance(scope, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,127}", scope) for scope in scopes)):
        raise p.SafeError("Token scope metadata was malformed; permissions are unknown.")
    if include_timing:
        issued_at = data.get("issued_at")
        if data.get("type") != "USER" or type(issued_at) is not int or issued_at <= 0:
            raise p.SafeError("Token issuance metadata is missing; refresh eligibility is unknown.")
        selected["issued_at"] = issued_at
    return selected


def status(args):
    current = runtime.load_config(args.config)
    previous = runtime.load_config(args.previous_config) if args.previous_config else None
    configs = [current] + ([previous] if previous else [])
    if any(config["platform"] != "threads" for config in configs):
        raise p.SafeError("This diagnostic supports Threads configs only.")
    if previous and previous["expected_user_id"] != current["expected_user_id"]:
        raise p.SafeError("Previous config must belong to the same configured account.")
    # APIClient fixes the HTTPS host/version, refuses redirects, and adds only
    # the CURRENT token as access_token. The inspected token is input_token.
    client = p.APIClient("threads", current["access_token"])
    output = {"current": inspect_token(client, current)}
    if previous:
        output["previous"] = inspect_token(client, previous)
    rendered = json.dumps(output, ensure_ascii=True)
    for config in configs:
        rendered = runtime.redact(rendered, config["access_token"])
    print(rendered)
    return 0


def main(argv=None):
    parser = runtime.PrivateArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", required=True)
    parser.add_argument("--previous-config")
    args = parser.parse_args(argv)
    try:
        return status(args)
    except p.ApiError:
        print(json.dumps({"error": "Official token inspection failed; validity and expiry are unknown."}))
        return 1
    except Exception:
        # Never print exception text: config/transport/remote errors may contain
        # credentials or a full request URL. No config or ledger is written.
        print(json.dumps({"error": "Token inspection could not complete safely; no files were changed."}))
        return 2
    except KeyboardInterrupt:
        print(json.dumps({"error": "Token inspection cancelled; no files were changed."}))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
