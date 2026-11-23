import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('builder', Path(__file__).with_name('build_plugin_zip.py'))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.run_git('init', '-q')
        package = self.root / builder.PREFIX
        (package / 'skills/setup').mkdir(parents=True)
        (package / 'skills/setup/SKILL.md').write_text('committed instructions')
        (package / 'plugin.json').write_text(json.dumps({'name': 'moru-sns', 'version': '0.1.2',
            'extensions': {'com.openai': {'onboardingSkill': './skills/setup/SKILL.md'}}}))
        self.commit()
        self.enterContext(patch.object(builder, 'ROOT', self.root))

    def run_git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.DEVNULL)

    def commit(self):
        self.run_git('add', '.')
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')

    def test_committed_only_root_layout_and_reproducible(self):
        package = self.root / builder.PREFIX
        (package / '.env').write_text('synthetic-untracked')
        (package / 'skills/setup/SKILL.md').write_text('uncommitted change')
        paths = [Path(self.temp.name) / f'{i}.zip' for i in range(2)]
        results = [builder.build('HEAD', p) for p in paths]
        self.assertEqual(results[0]['sha256'], results[1]['sha256'])
        with zipfile.ZipFile(paths[0]) as archive:
            self.assertEqual(set(archive.namelist()), {'plugin.json', 'skills/setup/SKILL.md'})
            self.assertEqual(archive.read('skills/setup/SKILL.md'), b'committed instructions')

    def test_tracked_symlink_rejected(self):
        (self.root / builder.PREFIX / 'link').symlink_to('/tmp/no-access-required')
        self.commit()
        with self.assertRaises(ValueError):
            builder.build('HEAD', Path(self.temp.name) / 'bad.zip')

    def test_tracked_state_rejected(self):
        (self.root / builder.PREFIX / 'publisher.sqlite3').write_bytes(b'fixture')
        self.commit()
        with self.assertRaises(ValueError):
            builder.build('HEAD', Path(self.temp.name) / 'bad.zip')


if __name__ == '__main__':
    unittest.main()
