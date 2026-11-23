#!/usr/bin/env python3
"""Check this distribution's local paths and skill/runtime payload offline."""
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {"sns-setup", "sns-dev-stories", "sns-direct-publisher", "sns-follow-review"}


def check(condition, message):
    if not condition:
        raise ValueError(message)


def inside(path, parent):
    check(path.resolve().is_relative_to(parent.resolve()), f"Path escapes package: {path}")
    check(not path.is_symlink(), f"Symlink is not a package file: {path}")
    check(path.exists(), f"Missing package path: {path}")


def main():
    marketplace = json.loads((ROOT / ".agents/plugins/marketplace.json").read_text())
    check(marketplace["name"] == "moru-sns", "Unexpected marketplace name")
    check(len(marketplace["plugins"]) == 1, "Expected exactly one plugin")
    entry = marketplace["plugins"][0]
    check(entry["source"]["source"] == "local", "Expected local package source")
    rel = entry["source"]["path"]
    check(rel.startswith("./") and ".." not in Path(rel).parts, "Invalid package path")
    plugin = ROOT / rel
    inside(plugin, ROOT)
    manifest = json.loads((plugin / "plugin.json").read_text())
    check(manifest["name"] == entry["name"] == "moru-sns", "Plugin name mismatch")
    check(manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "Unexpected manifest schema")
    check(re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), "Invalid version")
    onboarding = manifest["extensions"]["com.openai"]["onboardingSkill"]
    check(onboarding == "./skills/sns-setup/SKILL.md", "Unexpected onboarding skill")
    inside(plugin / onboarding, plugin)
    skills = plugin / "skills"
    check({p.name for p in skills.iterdir() if p.is_dir()} == EXPECTED, "Unexpected skill set")
    files = 0
    for name in sorted(EXPECTED):
        folder = skills / name
        source = folder / "SKILL.md"
        data = source.read_text(encoding="utf-8")
        check(data.startswith("---\n"), f"Missing frontmatter: {name}")
        front = data.split("---", 2)[1]
        check(f"name: {name}\n" in front, f"Skill name mismatch: {name}")
        check(re.search(r"^description: .+", front, re.M), f"Missing description: {name}")
        for path in folder.rglob("*"):
            inside(path, folder)
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            files += 1
            check(path.suffix not in {".sqlite", ".sqlite3", ".db", ".pem", ".key"}, f"Unexpected private/runtime file: {path.name}")
            if path.suffix == ".md":
                for link in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                    if ":" in link or link.startswith("#"):
                        continue
                    dest = link.split("#", 1)[0]
                    if dest:
                        inside(path.parent / dest, folder)
    runtime = skills / "sns-direct-publisher/scripts"
    for name in ("publisher.py", "private_runtime.py", "runtime_guard.py", "token_status.py", "threads_refresh.py"):
        check((runtime / name).is_file(), f"Missing runtime: {name}")
        compile((runtime / name).read_text(), name, "exec")
    remote_files = 0
    remote = plugin / "remote"
    if remote.exists():
        for path in remote.rglob("*"):
            inside(path, remote)
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            remote_files += 1
            check(path.suffix in {".py", ".md", ".txt"}, f"Unexpected remote payload: {path.name}")
            if path.suffix == ".py":
                compile(path.read_text(), path.name, "exec")
        for name in ("engine.py", "server.py", "requirements.txt", "README.md", "test_remote.py"):
            check((remote / name).is_file(), f"Missing remote transport file: {name}")
    print(json.dumps({"status": "ok", "plugin": manifest["name"], "version": manifest["version"], "skills": sorted(EXPECTED), "payload_files": files, "remote_files": remote_files, "network_calls": 0}))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as error:
        print(f"Package check failed: {error}", file=sys.stderr)
        raise SystemExit(1)
