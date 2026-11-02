"""Deterministic fault fixtures ONLY. Not model evaluation or benchmark scores."""
import asyncio
import errno
from unittest.mock import patch
from .agents import Oracle
from .environment import EvidenceDocker

class FaultOracle(Oracle):
    async def run(self, instruction, environment, context):
        if instruction.strip() in ('FAIL', 'CANDIDATE_TIMEOUT', 'CANCEL_CANDIDATE'):
            await environment.exec('echo partial > /workspace/working/current/partial.txt; touch /logs/agent/ready')
            if instruction.strip() == 'FAIL':
                raise RuntimeError('Injected Candidate execution failure')
            await asyncio.sleep(600)
        await super().run(instruction, environment, context)

class DiskFailureDocker(EvidenceDocker):
    async def export(self, name, *, candidate):
        if candidate:
            with patch('deliverbench_harbor.environment.inventory', side_effect=OSError(errno.ENOSPC, 'Injected full disk')):
                return await super().export(name, candidate=candidate)
        return await super().export(name, candidate=candidate)

from .lifecycle import Plugin
from .io import atomic_json
import json
class FixturePlugin(Plugin):
    """Explicitly labels local Responses fixtures; never used in scored configs."""
    async def on_job_start(self, job):
        await super().on_job_start(job)
        path = self.root / "protocol.json"
        data = json.loads(path.read_text())
        data.update(synthetic_fixture=True, not_benchmark_result=True)
        atomic_json(path, data)
