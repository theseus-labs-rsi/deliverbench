"""Public CLI JobPlugin: failure gate and explicit outcome records."""
from importlib.metadata import version
from pathlib import Path
import os
import sys
import harbor

from .environment import ACTIVE_JOBS, ACTIVE_TASKS
from .contract import PROTOCOL, load_task
from .io import atomic_json, persistent_job_storage


class Plugin:
    async def on_job_start(self, job):
        if version("harbor") != "0.20.0":
            raise RuntimeError("This release requires unmodified harbor==0.20.0")
        definitions = {}
        for task in job.config.tasks:
            if not getattr(task, "path", None):
                raise ValueError("This protocol version requires local task packages")
            definitions[str(Path(task.path).resolve())] = load_task(task.path)
        if not definitions:
            raise ValueError("No DeliverBench tasks configured")
        # Native uv-script subprocess uses exactly the Python that owns Harbor
        # and this extension, rather than downloading a second package version.
        os.environ["DELIVERBENCH_PYTHON"] = sys.executable
        for agent in job.config.agents:
            implementation = agent.import_path or agent.name or ""
            if not implementation.startswith("deliverbench_harbor."):
                raise ValueError("Use a checked Agent, e.g. -a deliverbench_harbor.agents:Codex")
            if (any(d.get("kickoff") for d in definitions.values())
                    and not implementation.endswith(("Oracle", ":Codex", ":CodexGateway", ":ClaudeCode", ":ClaudeGateway", ":QwenCode"))):
                raise ValueError("Project briefing requires a supported native Agent or Oracle")
            if not implementation.endswith("Oracle"):
                if not agent.resume_trajectory:
                    raise ValueError("Continuous Candidate evaluation requires --resume-trajectory")
                if not agent.model_name:
                    raise ValueError("Set model_name in YAML, or supply BOTH -a and -m (Harbor 0.20 CLI)")
        if job.config.verifier.import_path != "deliverbench_harbor.verifier:CompleteVerifier":
            raise ValueError("Use the shipped job config with CompleteVerifier")
        production = any(
            getattr(t, "path", None) and (Path(t.path) / "tests/steps").is_dir()
            for t in job.config.tasks
        )
        if production and os.environ.get("DELIVERBENCH_INFRA_PROBE", "").lower() != "true":
            from harbor.utils.env import resolve_env_vars
            overrides = resolve_env_vars(job.config.verifier.env)
            missing = [k for k in ("JUDGE_API_KEY", "JUDGE_BASE_URL", "JUDGE_MODEL")
                       if not overrides.get(k, os.environ.get(k))]
            if missing:
                raise ValueError("Set Judge configuration before starting Candidate: " + ", ".join(missing))
        if any(d.get("response_events") for d in definitions.values()):
            from .response_client import effective_config
            from harbor.utils.env import resolve_env_vars
            for key,value in resolve_env_vars(job.config.verifier.env).items():
                if key.startswith('JUDGE_') and value:
                    os.environ.setdefault(key,str(value))
            response_config=effective_config()
            if not os.environ.get('DELIVERBENCH_RESPONSE_FIXTURES'):
                if not all(response_config[k] for k in ('base_url','api_key','model')):
                    raise ValueError('Set RESPONDER_* or JUDGE_* before starting dynamic tasks')
            response_config={k:v for k,v in response_config.items() if k!='api_key'}
            response_config['synthetic_fixture']=bool(os.environ.get('DELIVERBENCH_RESPONSE_FIXTURES'))
            atomic_json(job.job_dir / 'responder_configuration.json', response_config)
        self.root = job.job_dir.resolve()
        ACTIVE_JOBS.add(self.root)
        ACTIVE_TASKS[self.root] = definitions
        atomic_json(self.root / "protocol.json", {
            "protocol": PROTOCOL, "harbor_version": version("harbor"),
            "persistent_evidence_directory": str(persistent_job_storage(self.root)),
            "harbor_module": str(Path(harbor.__file__).resolve()),
            "synthetic_fixture": bool(os.environ.get('DELIVERBENCH_RESPONSE_FIXTURES')) or any((a.import_path or a.name or "").startswith(
                "deliverbench_harbor.testing:") for a in job.config.agents),
        })

        async def before_judge(event):
            step = (event.result.step_results or [])[-1]
            if step.exception_info:
                raise RuntimeError("Candidate failed; grading and later stages are blocked")

        async def ended(event):
            from .acceptance import summarize_trial
            from .gateway import close_router
            close_router(event.trial_id)
            trial = event.config.trials_dir / event.trial_name
            try:
                status = summarize_trial(event.result.model_dump(mode="json"), trial)
            except (OSError, ValueError, KeyError, TypeError):
                status = {"status": "invalid_contract", "pipeline_complete": False,
                          "scoring_complete": False, "final_score": None}
            atomic_json(trial / "delivery_status.json", status)

        async def cancelled(event):
            from .gateway import close_router
            close_router(event.trial_id)
            trial = event.config.trials_dir / event.trial_name
            atomic_json(trial / "cancellation.json", {"status": "cancelled", "trial": event.trial_name})

        job.on_verification_started(before_judge)
        job.on_trial_ended(ended)
        job.on_trial_cancelled(cancelled)

    async def on_job_end(self, result):
        from .acceptance import check_job
        atomic_json(self.root / "delivery_summary.json", check_job(self.root))
        ACTIVE_JOBS.discard(self.root)
        ACTIVE_TASKS.pop(self.root, None)
