#!/usr/bin/env python3
"""Run local protocol and packaging checks without model APIs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    data = args.data_dir.resolve()
    out = args.output_dir.resolve()
    if out.is_relative_to(ROOT / 'deliverbench-harbor') or out.is_relative_to(data):
        p.error('The output directory must be outside the extension and dataset directories')
    out.mkdir(parents=True, exist_ok=False)
    # Local setuptools installs leave build and egg-info directories in the checkout.
    # Audit a clean distribution view without deleting those local build artifacts.
    clean_extension = out / 'extension_source'
    shutil.copytree(ROOT / 'deliverbench-harbor', clean_extension,
                    ignore=shutil.ignore_patterns('build', '*.egg-info', '__pycache__', '.DS_Store'))
    qa = clean_extension / 'qa'
    projects = json.loads((ROOT / 'data_release.json').read_text())['projects']
    commands = []
    for name in ['background_checks', 'bonus_checks', 'kickoff_checks', 'material_release_checks', 'response_checks']:
        commands.append((name, [sys.executable, str(qa / (name + '.py'))]))
    for pid, project in projects.items():
        task = data / project['directory']
        for name in ['schema_checks', 'unit_checks', 'cumulative_checks', 'release_check']:
            commands.append((pid + '-' + name, [sys.executable, str(qa / (name + '.py')), '--project', str(task)]))
    commands.append(('language_hurdle_checks', [sys.executable, str(qa / 'language_hurdle_checks.py'),
                                              '--project', str(data / projects['case-01']['directory'])]))
    commands.append(('download_checks', [sys.executable, str(ROOT / 'tests/test_download_data.py')]))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENAI_', 'ANTHROPIC_', 'JUDGE_', 'RESPONDER_'))}
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    checks = []
    for name, command in commands:
        with (out / (name + '.log')).open('w') as f:
            result = subprocess.run(command, env=env, cwd=out, stdout=f, stderr=subprocess.STDOUT)
        checks.append({'check': name, 'passed': result.returncode == 0})
        print(name, 'PASS' if result.returncode == 0 else 'FAIL', flush=True)
    report = {'passed': all(c['passed'] for c in checks), 'model_calls': 0, 'checks': checks}
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
