"""Preserve failed evidence before invoking stock Harbor job recovery."""
import argparse,datetime,json,shutil,subprocess,sys
from pathlib import Path
from .io import inventory,atomic_json,persistent_job_storage

def preserve(job,types):
    job=Path(job).resolve()
    if not (job/'config.json').is_file():raise ValueError('Not a Harbor job directory')
    selected=[]
    for result in sorted(job.glob('*/result.json')):
        data=json.loads(result.read_text());error=data.get('exception_info') or {}
        if error.get('exception_type') not in types:continue
        message=json.dumps(error,ensure_ascii=False).lower()
        if any(t in message for t in ('content_filter','inappropriate content','provider refused')):
            raise ValueError('Provider refusal requires a supported resolution; automatic recovery refused')
        selected.append(result.parent)
    if not selected:raise ValueError('No matching failed trials; valid low scores are not recovery targets')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=persistent_job_storage(job)/'recovery_evidence'/stamp;backup.mkdir(parents=True)
    for name in ('config.json','result.json','protocol.json','delivery_summary.json','responder_configuration.json'):
        if (job/name).is_file():shutil.copy2(job/name,backup/name)
    rows=[]
    for trial in selected:
        destination=backup/'trials'/trial.name
        shutil.copytree(trial,destination,symlinks=True)
        original=inventory(trial);copied=inventory(destination)
        if original!=copied:raise RuntimeError('Recovery backup verification failed; original remains untouched')
        rows.append({'trial':trial.name,'files':original})
    for name in ('response_store','recovery_evidence'):
        legacy=job/name
        if legacy.is_dir() and not legacy.is_symlink():
            saved=backup/'legacy_job_sidecars'/name
            shutil.copytree(legacy,saved,symlinks=True)
            if inventory(legacy)!=inventory(saved):
                raise RuntimeError('Legacy sidecar backup verification failed')
    atomic_json(backup/'manifest.json',{'schema_version':1,'recovery_kind':'native_job_retry','selected_errors':types,
        'preserved_trials':rows,'response_policy':'reuse only byte-identical inputs, fact/rule versions, backend and response engine',
        'candidate_policy':'stock Harbor starts a new attempt; this is not in-place continuation of a cancelled native process'})
    return backup

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job-path',required=True,type=Path)
    parser.add_argument('--filter-error-type',action='append',default=None)
    args=parser.parse_args();types=args.filter_error_type or ['CancelledError']
    backup=preserve(args.job_path,types);print('Verified preserved evidence:',backup,flush=True)
    command=[str(Path(sys.executable).with_name('harbor')),'jobs','resume','--job-path',str(args.job_path.resolve()),
             '--plugin','deliverbench_harbor.lifecycle:Plugin']
    for name in types:command+=['--filter-error-type',name]
    raise SystemExit(subprocess.call(command))

if __name__=='__main__':main()
