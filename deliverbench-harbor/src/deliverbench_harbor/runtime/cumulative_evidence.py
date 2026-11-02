"""Build stage-bounded raw evidence from host-attested immutable submissions."""
import hashlib
import json
import shutil
from pathlib import Path

from rubric_schema import canonical_path


def read(path):
    return json.loads(path.read_text())


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root):
    rows = []
    exists = root.is_dir() and not root.is_symlink()
    if exists:
        for p in sorted(root.rglob('*')):
            kind = 'symlink' if p.is_symlink() else 'file' if p.is_file() else 'directory'
            rows.append({'path':p.relative_to(root).as_posix(), 'type':kind,
                         **({'sha256':digest(p)} if kind == 'file' else {})})
    return {'exists':exists, 'entries':rows}


def checked_path(root, relative):
    canonical_path(relative)
    p = root
    for part in Path(relative).parts:
        p = p / part
        if p.is_symlink():
            raise ValueError('Symlink in trusted source path: ' + relative)
    return p


def copy_tree(source, target):
    """Never follow submitted symlinks; inventory retains their existence."""
    if source.is_symlink():
        return
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
        for child in sorted(source.iterdir()):
            copy_tree(child, target / child.name)
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def build_view(contract, workspace, unit, destination):
    workspace, destination = Path(workspace), Path(destination)
    steps = contract['steps']
    if unit != 'FINAL' and unit not in steps:
        raise ValueError('Unknown evaluation unit')
    cutoff = len(steps)-1 if unit == 'FINAL' else steps.index(unit)
    if contract['current_step'] != steps[cutoff]:
        raise ValueError('Evidence cutoff disagrees with runtime stage')
    destination.mkdir(parents=True, exist_ok=True)
    checkpoint_root = checked_path(workspace, 'working/checkpoints')
    checked_path(workspace, '.checkpoints')
    checked_path(workspace, 'deliverables/final')
    receipts = {}
    for stage in steps[:cutoff+1]:
        receipt_path = workspace / '.checkpoints' / (stage+'_deliverbench_output.json')
        if not receipt_path.is_file() or receipt_path.is_symlink():
            raise ValueError('Missing trusted stage receipt: ' + stage)
        record = read(receipt_path)
        if (record.get('step') != stage or record.get('candidate_completed') is not True
                or record.get('attestation') != 'host_native_success_v1'):
            raise ValueError('Stage did not complete normally: ' + stage)
        checked_path(workspace, 'working/checkpoints/'+stage)
        state = snapshot(checkpoint_root / stage)
        if not state['exists'] or state != record.get('checkpoint_directory_snapshot'):
            raise ValueError('Frozen checkpoint changed: ' + stage)
        receipts[stage] = record
    final_state = snapshot(workspace/'deliverables/final')
    if final_state != receipts[steps[cutoff]].get('final_directory_snapshot'):
        raise ValueError('Final directory changed after stage completion')
    submission = workspace/'deliverables/final' if unit == 'FINAL' else checkpoint_root/unit
    copy_tree(submission, destination/'submission')
    (destination/'submission').mkdir(exist_ok=True)
    history = steps[:cutoff+1] if unit == 'FINAL' else steps[:cutoff]
    for stage in history:
        copy_tree(checkpoint_root/stage, destination/'history'/stage)
    sources = []
    for row in contract['input_files']:
        if row['visible_from'] not in steps:
            raise ValueError('Unknown material release stage')
        if steps.index(row['visible_from']) > cutoff:
            continue
        src = checked_path(workspace, 'source/'+row['candidate_path'])
        if not src.is_file() or digest(src) != row['sha256']:
            raise ValueError('Released source missing or changed: ' + row['candidate_path'])
        copy_tree(src, destination/'sources'/row['candidate_path'])
        sources.append({k:row[k] for k in ('candidate_path','visible_from','sha256')})
    for event in contract.get('response_events', []):
        if steps.index(event['stage']) > cutoff:
            continue
        receipt_path = checked_path(workspace, '.checkpoints/responses/'+event['id']+'.json')
        if not receipt_path.is_file():
            raise ValueError('Missing committed dynamic material: '+event['id'])
        receipt = read(receipt_path)
        if (receipt.get('status') != 'committed' or receipt.get('stage') != event['stage']
                or receipt.get('id') != event['id']):
            raise ValueError('Invalid dynamic material receipt')
        for relative, expected in receipt['inputs'].items():
            source = checked_path(workspace, relative)
            if (digest(source) if source.is_file() else None) != expected:
                raise ValueError('Dynamic response producer changed: '+relative)
        names = [row['path'] for row in receipt['files']]
        if len(names) != len(set(names)) or not set(names) <= set(event['outputs']):
            raise ValueError('Undeclared dynamic material')
        for row in receipt['files']:
            src = checked_path(workspace, 'source/'+row['path'])
            if not src.is_file() or digest(src) != row['sha256']:
                raise ValueError('Dynamic material missing or changed')
            copy_tree(src, destination/'sources'/row['path'])
            sources.append({'candidate_path':row['path'],'visible_from':event['stage'],
                            'sha256':row['sha256'],'response_event':event['id']})
        # A minimal publication receipt is evidence, never the private matching/review trace.
        dump(destination/'response_receipts'/(event['id']+'.json'), receipt)
    dump(destination/'source_catalog.json', sources)
    prompts = contract['stage_instructions']
    if set(prompts) != set(steps[:cutoff+1]):
        raise ValueError('Stage instruction visibility mismatch')
    folder = destination/'instructions';folder.mkdir()
    (folder/'project.md').write_text(contract['project_instruction'])
    for stage in steps[:cutoff+1]:
        (folder/(stage+'.md')).write_text(prompts[stage])
    # Keep observable read-only history checks; do not extract document contents.
    for stage in steps[:cutoff+1]:
        protected = contract.get('protected_history',{}).get(stage,[])
        if not protected:
            continue
        input_record = workspace/'.checkpoints'/(stage+'_input.json')
        if not input_record.is_file() or input_record.is_symlink():
            raise ValueError('Missing historical input snapshot: ' + stage)
        expected = {r['path']:r['sha256'] for r in read(input_record)['files']}
        rows = []
        for prior in protected:
            if prior not in steps[:steps.index(stage)]:
                raise ValueError('Protected history must precede stage')
            for entry in snapshot(checkpoint_root/prior)['entries']:
                if entry['type'] != 'file':
                    continue
                key = 'working/checkpoints/'+prior+'/'+entry['path']
                rows.append({'path':key,'before_sha256':expected.get(key),
                             'captured_sha256':entry['sha256'],
                             'same_bytes':entry['sha256']==expected[key] if key in expected else None})
        dump(destination/'observations'/(stage+'_readonly_history.json'),{'records':rows,'no_claim_when_before_missing':True})
    manifest = {'evaluation_unit':unit,'evidence_mode':'cumulative_raw',
                'visible_through':steps[cutoff],'history_stages':history,
                'current_submission_root':'submission/',
                'submission_snapshot':snapshot(submission),
                'history_snapshots':{s:snapshot(checkpoint_root/s) for s in history},
                'source_count':len(sources),
                'workspace_mapping':{'/workspace/source/':'sources/',
                    '/workspace/working/checkpoints/':'history/',
                    '/workspace/deliverables/final/':'submission/' if unit=='FINAL' else 'not_current_submission'},
                'historical_files_cannot_replace_current_submission':True}
    if unit != 'FINAL':
        manifest['workspace_mapping']['/workspace/working/checkpoints/'+unit+'/'] = 'submission/'
        manifest['workspace_mapping']['/workspace/working/current/'] = 'submission/'
    if unit == 'FINAL':
        manifest['final_rubric_sources'] = contract['final_rubric_sources']
        manifest['source_file_mapping'] = contract['source_groups']
    if unit == 'FINAL' or unit == steps[-1]:
        dump(destination/'final_directory_inventory.json', final_state)
    dump(destination/'evaluation_manifest.json', manifest)
    return manifest
