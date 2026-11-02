"""Synthetic download integrity checks; contains no benchmark judgments or results."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('download_data', Path(__file__).resolve().parents[1] / 'scripts/download_data.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'snapshot'
        self.out = self.root / 'destination'
        self.manifest = {'repo_id': 'deliverbench/DeliverBench', 'revision': 'a' * 40, 'projects': {}}
        for pid in ['case-01', 'case-02']:
            name = pid.lower() + '_synthetic'
            p = self.source / 'projects' / name / 'task/task.toml'
            p.parent.mkdir(parents=True)
            p.write_text('synthetic = true\n')
            self.manifest['projects'][pid] = {'directory': name, 'files': [
                {'path': 'task/task.toml', 'sha256': d.sha256(p), 'bytes': p.stat().st_size}]}

    def install(self, selected=None):
        return d.install_projects(self.manifest, selected or ['case-01', 'case-02'], self.out, self.source)

    def test_all_and_identical_reuse(self):
        self.assertEqual(self.install()['installed'], ['case-01', 'case-02'])
        p = self.out / 'case-01_synthetic/task/task.toml'
        before = p.stat().st_mtime_ns
        self.assertEqual(self.install()['reused'], ['case-01', 'case-02'])
        self.assertEqual(before, p.stat().st_mtime_ns)

    def test_selection(self):
        self.install(['case-02'])
        self.assertFalse((self.out / 'case-01_synthetic').exists())

    def test_changed_existing_is_preserved(self):
        self.install()
        p = self.out / 'case-01_synthetic/task/task.toml'
        p.write_text('local edit')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(p.read_text(), 'local edit')

    def test_extra_run_file_is_preserved(self):
        self.install()
        p = self.out / 'case-01_synthetic/run.json'
        p.write_text('local run')
        with self.assertRaises(ValueError): self.install()
        self.assertEqual(p.read_text(), 'local run')

    def test_hash_failure_installs_nothing(self):
        (self.source / 'projects/case-02_synthetic/task/task.toml').write_text('tampered')
        with self.assertRaises(ValueError): self.install()
        self.assertFalse((self.out / 'case-01_synthetic').exists())

    def test_missing_source(self):
        (self.source / 'projects/case-02_synthetic/task/task.toml').unlink()
        with self.assertRaises(ValueError): self.install()

    def test_source_symlink(self):
        p = self.source / 'projects/case-01_synthetic/task/task.toml'
        p.unlink(); p.symlink_to(self.source / 'projects/case-02_synthetic/task/task.toml')
        with self.assertRaises(ValueError): self.install()

    def test_destination_symlink(self):
        self.out.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(ValueError): self.install()

    def test_traversal_manifest(self):
        self.manifest['projects']['case-01']['files'][0]['path'] = '../escape'
        with self.assertRaises(ValueError): self.install()

    def test_duplicate_manifest(self):
        self.manifest['projects']['case-01']['files'] *= 2
        with self.assertRaises(ValueError): self.install()

    def test_wrong_repository(self):
        self.manifest['repo_id'] = 'unexpected/repo'
        with self.assertRaises(ValueError): self.install()

    def test_immutable_revision_required(self):
        for revision in ('main', '', None):
            with self.subTest(revision=revision):
                self.manifest['revision'] = revision
                with self.assertRaises(ValueError):
                    d.install_projects(self.manifest, ['case-01'], self.out)

    def test_remote_download_is_pinned(self):
        with patch('huggingface_hub.snapshot_download', return_value=str(self.source)) as fn:
            d.install_projects(self.manifest, ['case-01'], self.out)
        kw = fn.call_args.kwargs
        self.assertEqual(kw['repo_type'], 'dataset')
        self.assertEqual(kw['revision'], 'a' * 40)
        self.assertEqual(kw['allow_patterns'], ['projects/case-01_synthetic/**'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
