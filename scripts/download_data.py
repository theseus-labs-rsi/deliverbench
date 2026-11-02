#!/usr/bin/env python3
"""Download the pinned public tasks without overwriting local edits or run files."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def safe_relative(name):
    if not isinstance(name, str) or not name or '\\' in name or '\x00' in name:
        raise ValueError('Invalid path in release manifest')
    parts = name.split('/')
    if PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in parts):
        raise ValueError('Unsafe path in release manifest: ' + repr(name))
    return Path(*parts)


def validate_manifest(manifest):
    if manifest.get('repo_id') != 'deliverbench/DeliverBench':
        raise ValueError('Unexpected dataset repository')
    if not isinstance(manifest.get('projects'), dict) or not manifest['projects']:
        raise ValueError('No project entries')
    directories = set()
    for pid, project in manifest['projects'].items():
        if pid not in ('case-01', 'case-02', 'case-03'):
            raise ValueError('Unknown project ID')
        directory = project['directory']
        if len(safe_relative(directory).parts) != 1 or directory in directories:
            raise ValueError('Invalid or duplicate project directory')
        directories.add(directory)
        paths = set()
        for row in project['files']:
            safe_relative(row['path'])
            if row['path'] in paths or not re.fullmatch(r'[0-9a-f]{64}', row['sha256']):
                raise ValueError('Duplicate path or invalid digest')
            if type(row['bytes']) is not int or row['bytes'] < 0:
                raise ValueError('Invalid file size')
            paths.add(row['path'])
        if not paths or 'task/task.toml' not in paths:
            raise ValueError('Incomplete project manifest')


def verify_tree(directory, rows):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Project is not a regular directory: ' + str(directory))
    actual = set()
    for p in directory.rglob('*'):
        if p.is_symlink():
            raise ValueError('Symlinks are not accepted: ' + str(p))
        if p.is_file():
            actual.add(p.relative_to(directory).as_posix())
    expected = {row['path'] for row in rows}
    if actual != expected:
        raise ValueError('File set mismatch in ' + str(directory) + '; missing=' +
                         repr(sorted(expected - actual)) + '; extra=' + repr(sorted(actual - expected)))
    for row in rows:
        p = directory / safe_relative(row['path'])
        if p.stat().st_size != row['bytes'] or sha256(p) != row['sha256']:
            raise ValueError('SHA256/size mismatch: ' + str(p))


def install_projects(manifest, selected, output_dir, source_dir=None):
    validate_manifest(manifest)
    selected = list(dict.fromkeys(selected))
    if not selected or any(p not in manifest['projects'] for p in selected):
        raise ValueError('Unknown project selection')
    output_dir = Path(output_dir).absolute()
    if any(p.is_symlink() for p in (output_dir, *output_dir.parents)):
        raise ValueError('Output path cannot contain symlinks')
    output_dir.mkdir(parents=True, exist_ok=True)
    pending = []
    for pid in selected:
        project = manifest['projects'][pid]
        target = output_dir / project['directory']
        if target.exists() or target.is_symlink():
            verify_tree(target, project['files'])
        else:
            pending.append(pid)
    if not pending:
        return {'installed': [], 'reused': selected}

    with tempfile.TemporaryDirectory(prefix='.deliverbench-download-', dir=output_dir) as td:
        temp = Path(td)
        if source_dir is None:
            revision = manifest.get('revision', '')
            if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
                raise ValueError('Release has no immutable dataset commit')
            from huggingface_hub import snapshot_download
            snapshot = Path(snapshot_download(
                repo_id=manifest['repo_id'], repo_type='dataset', revision=revision,
                local_dir=temp / 'snapshot',
                allow_patterns=['projects/' + manifest['projects'][pid]['directory'] + '/**'
                                for pid in pending],
            ))
        else:
            snapshot = Path(source_dir).absolute()
            if any(p.is_symlink() for p in (snapshot, *snapshot.parents)):
                raise ValueError('Source snapshot path cannot contain symlinks')
        if (snapshot / 'projects').is_symlink():
            raise ValueError('Source project root cannot be a symlink')

        # Verify every requested project before installing any new directory.
        for pid in pending:
            project = manifest['projects'][pid]
            source = snapshot / 'projects' / project['directory']
            verify_tree(source, project['files'])
            staged = temp / 'ready' / project['directory']
            shutil.copytree(source, staged)
            verify_tree(staged, project['files'])
        for pid in pending:
            directory = manifest['projects'][pid]['directory']
            target = output_dir / directory
            if target.exists() or target.is_symlink():
                raise ValueError('Target appeared during download; refusing overwrite: ' + str(target))
            os.rename(temp / 'ready' / directory, target)
    return {'installed': pending, 'reused': [p for p in selected if p not in pending]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', choices=['all', 'case-01', 'case-02', 'case-03'], default='all')
    p.add_argument('--output-dir', type=Path, required=True)
    p.add_argument('--source-dir', type=Path, help='Read an existing Hub snapshot instead of downloading')
    args = p.parse_args()
    manifest = json.loads((ROOT / 'data_release.json').read_text())
    selected = list(manifest['projects']) if args.project == 'all' else [args.project]
    try:
        result = install_projects(manifest, selected, args.output_dir, args.source_dir)
    except (OSError, ValueError) as exc:
        p.exit(1, 'Download refused: ' + str(exc) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
