#!/usr/bin/env python3
"""Export only committed plugin files, with the portable manifest at ZIP root."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'plugins/moru-sns/'


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def build(ref, output):
    commit = git('rev-parse', '--verify', '--end-of-options', ref + '^{commit}').decode().strip()
    entries = []
    for entry in git('ls-tree', '-rz', commit, '--', PREFIX).split(b'\0'):
        if not entry:
            continue
        meta, path = entry.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        name = path.decode().removeprefix(PREFIX)
        if mode not in ('100644', '100755') or kind != 'blob':
            raise ValueError('Only regular tracked files may enter the package')
        if '..' in Path(name).parts or name.startswith('/'):
            raise ValueError('Invalid archive path')
        if any(part.startswith('.env') or part == '__pycache__' for part in Path(name).parts) or Path(name).suffix in {'.db', '.sqlite', '.sqlite3', '.pem', '.key', '.pyc'}:
            raise ValueError('Private or generated file in package')
        entries.append((name, git('cat-file', 'blob', oid)))
    payload = dict(entries)
    manifest = json.loads(payload['plugin.json'])
    onboarding = manifest['extensions']['com.openai']['onboardingSkill'].removeprefix('./')
    if onboarding not in payload or manifest['name'] != 'moru-sns':
        raise ValueError('Missing onboarding or incorrect package identity')
    output = Path(output).resolve()
    if output.is_relative_to(ROOT):
        raise ValueError('Write release artifacts outside the source checkout')
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return {'commit': commit, 'version': manifest['version'], 'files': len(entries),
            'sha256': hashlib.sha256(output.read_bytes()).hexdigest(), 'output': str(output)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.ref, args.output)))
