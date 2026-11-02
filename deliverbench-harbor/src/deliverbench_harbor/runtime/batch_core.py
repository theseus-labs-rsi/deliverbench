"""Batch scheduling and external dependency arithmetic; no model calls here."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json

PROTOCOL = 'deliverbench-batch8-v1'


def stable_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def plan_batches(items, batch_size=8, evidence_key=lambda item: None):
    if batch_size < 1:
        raise ValueError('batch_size must be positive')
    ids = [r['id'] for r in items]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Empty or duplicate rubric IDs')
    groups = defaultdict(list)
    for item in items:
        # Dependencies never constrain scheduling or batch membership.
        groups[stable_hash(evidence_key(item))].append(item)
    return [group[i:i + batch_size] for group in groups.values()
            for i in range(0, len(group), batch_size)]


def run_batches(items, invoke, batch_size=8, concurrency=4, evidence_key=lambda item: None):
    """Rolling pool. A failed batch keeps accepted IDs; missing IDs get one singleton retry."""
    if concurrency < 1:
        raise ValueError('concurrency must be positive')
    initial = plan_batches(items, batch_size, evidence_key)
    results, records = {}, []

    def submit(pool, group, label, fallback):
        return pool.submit(invoke, group, label, fallback)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        pending = {submit(pool, group, f'b{i:04}', False): (group, False)
                   for i, group in enumerate(initial, 1)}
        while pending:
            future = next(as_completed(pending))
            group, fallback = pending.pop(future)
            try:
                accepted, record = future.result()
            except Exception as exc:
                accepted, record = {}, {'ids': [r['id'] for r in group], 'error': str(exc)}
            records.append(record)
            for item in group:
                rid = item['id']
                if rid in accepted:
                    results.setdefault(rid, accepted[rid])
                elif not fallback and len(group) > 1:
                    label = 'r-' + stable_hash(rid)[:16]
                    pending[submit(pool, [item], label, True)] = ([item], True)
    return results, records


def accept_rows(payload, items, validate, id_key='rubric_id'):
    """Validate independently, never coerce booleans or silently overwrite IDs."""
    rows = payload.get('verdicts') if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}, {'batch': 'Expected verdicts array'}
    expected = {r['id']: r for r in items}
    counts = defaultdict(int)
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get(id_key), str):
            return {}, {'batch': 'Invalid row identity'}
        rid = row[id_key]
        if rid not in expected:
            return {}, {'batch': 'Unknown rubric ID: ' + rid}
        counts[rid] += 1
    accepted, errors = {}, {}
    for rid, count in counts.items():
        if count != 1:
            errors[rid] = 'Duplicate verdict ID'
    for row in rows:
        rid = row[id_key]
        if rid in errors:
            continue
        try:
            result = validate(row, expected[rid])
            if result.get('status', 'graded') != 'graded':
                raise ValueError('Ungradable: ' + result.get('reason', result.get('justification', '')))
            accepted[rid] = result
        except (ValueError, TypeError, KeyError) as exc:
            errors[rid] = str(exc)
    for rid in expected.keys() - accepted.keys() - errors.keys():
        errors[rid] = 'Missing verdict'
    return accepted, errors


def dependency_gates(items, raw_hits):
    """All graded first. Gate positive points externally; never rewrite raw hits.

    depends_on defaults to all-hit DAG gating. Explicit analytic_only remains
    metadata-only. Negative penalties and hurdles are never suppressed. Cycles,
    unknown references and incomplete decisions are contract errors, not misses.
    """
    byid = {r['id']: r for r in items}
    if len(byid) != len(items) or set(raw_hits) != set(byid):
        raise ValueError('Dependency scoring requires complete unique verdicts')
    if any(type(hit) is not bool for hit in raw_hits.values()):
        raise ValueError('Dependency scoring requires strict booleans')
    active, effective, gates = set(), {}, {}

    def resolve(rid):
        if rid not in byid:
            raise ValueError('Unknown dependency: ' + str(rid))
        if rid in active:
            raise ValueError('Cyclic dependency: ' + rid)
        if rid in effective:
            return effective[rid]
        active.add(rid)
        item = byid[rid]
        deps = item.get('depends_on', [])
        if not isinstance(deps, list) or any(not isinstance(x, str) for x in deps):
            raise ValueError('depends_on must be an ID list')
        if any(d not in byid for d in deps):
            raise ValueError('Unknown dependency for ' + rid)
        mode = item.get('dependency_mode', 'all_hit')
        if deps and mode not in ('all_hit', 'analytic_only'):
            raise ValueError('Unsupported dependency_mode: ' + str(mode))
        # Visit all parents to detect cycles regardless of their hit values.
        parents = [resolve(d) for d in deps] if mode != 'analytic_only' else []
        gate = all(parents) if item['weight'] > 0 and not item.get('hurdle') else True
        gates[rid] = gate
        effective[rid] = raw_hits[rid] and gate
        active.remove(rid)
        return effective[rid]

    for rid in byid:
        resolve(rid)
    return gates
