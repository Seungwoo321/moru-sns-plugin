"""Build a minimal source upload directory. Never connects to or deploys to Vercel."""
from pathlib import Path
import argparse
import hashlib
import json


def prepare(destination):
    root = Path(__file__).resolve().parent
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    sources = {'api/index.py': root/'api/index.py', 'vercel.json': root/'vercel.json',
               'requirements.txt': root/'requirements.txt', 'privacy.py': root.parent/'privacy.py'}
    manifest = {}
    for name, source in sources.items():
        data = source.read_bytes()
        target = destination/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest[name] = hashlib.sha256(data).hexdigest()
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', help='New directory; existing directories are refused')
    print(json.dumps(prepare(parser.parse_args().destination), indent=2))
