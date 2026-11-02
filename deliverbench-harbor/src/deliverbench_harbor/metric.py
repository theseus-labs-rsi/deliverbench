# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Aggregate native mean rewards without presenting incomplete runs as scores."""
import argparse
import json
from statistics import mean
from pathlib import Path


def clip(value):
    return max(0., min(1., value))


def score_trial(rewards, contract):
    rewards = rewards if isinstance(rewards, dict) else {}
    steps = contract['steps']
    observed = [s for s in steps if 'db_seen_' + s in rewards]
    n = len(observed)
    # Harbor averages the union of reward keys over executed steps.
    value = lambda key: float(rewards.get(key, 0)) * n
    infrastructure = n == len(steps) and all(value('db_complete_' + s) > .5 and value('db_evidence_' + s) > .5 for s in steps)
    graded_steps = [s for s in observed if value('db_graded_' + s) > .5]
    scoring = infrastructure and len(graded_steps) == len(steps) and value('db_final_graded') > .5
    reference = value('db_reference_run') > 0
    fixture = value('db_non_model_probe') > 0 or value('db_synthetic_fixture') > 0
    actual = scoring and not reference and not fixture
    scores = {s: clip(value('db_score_' + s)) for s in graded_steps}
    result = {
        'reward': clip(value('db_final_score')) if actual else None,
        'final_delivery_quality': clip(value('db_final_score')) if actual else None,
        'final_delivery_quality_raw': clip(value('db_final_raw')) if actual else None,
        'process_delivery_quality': mean(scores.values()) if actual else None,
        'final_artifact_quality': clip(value('db_artifact')) if actual else None,
        'cross_file_coherence': clip(value('db_cross_file')) if actual else None,
        'package_delivery': clip(value('db_package_delivery')) if actual else None,
        'infrastructure_complete': float(infrastructure),
        'scoring_complete': float(scoring),
        'actual_model_evaluation': float(actual),
        'reference_run': float(reference), 'non_model_probe': float(fixture),
        'final_hurdle': float(value('db_final_hurdle') > .5) if actual else None,
        'observed_steps': n, 'graded_steps': len(graded_steps), 'expected_steps': len(steps),
    }
    # Stage-level results remain observable on genuinely graded partial runs.
    result.update({'step_score_' + s: v for s, v in scores.items()} if not reference and not fixture else {})
    for dim in ['correctness', 'completeness', 'consistency', 'professionalism', 'usability']:
        possible = value('db_final_dim_' + dim + '_possible')
        result['final_dimension_' + dim] = clip(value('db_final_dim_' + dim + '_earned') / possible) if possible and actual else None
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--contract', type=Path, required=True)
    parser.add_argument('-i', '--input-path', type=Path, required=True)
    parser.add_argument('-o', '--output-path', type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text())
    rows = [score_trial(json.loads(line), contract) for line in args.input_path.read_text().splitlines() if line.strip()]
    keys = set().union(*(r.keys() for r in rows)) if rows else {'reward'}
    # Harbor metrics accept numeric values only. Unavailable scores are omitted.
    result = {key: mean([r[key] for r in rows if r.get(key) is not None]) for key in sorted(keys) if any(r.get(key) is not None for r in rows)}
    args.output_path.write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
