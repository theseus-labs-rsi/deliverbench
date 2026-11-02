"""Require a committed, complete verifier result; numerical zero is valid."""
import json
from harbor.verifier.verifier import Verifier


class CompleteVerifier(Verifier):
    async def verify(self):
        from .contract import trial_contract
        contract = trial_contract(self.trial_paths.trial_dir)
        result = await super().verify()
        status_path = self.trial_paths.verifier_dir / "evaluation_status.json"
        status = json.loads(status_path.read_text()) if status_path.is_file() else {}
        step = self.step_name
        rewards = result.rewards or {}
        if status.get("status") not in ("graded", "infra_probe"):
            raise RuntimeError("Judge did not commit a complete result")
        required = ["db_protocol_valid", "db_complete_" + step]
        if status["status"] == "graded":
            required.append("db_graded_" + step)
            if step == contract["final_step"]:
                required.append("db_final_graded")
        if any(rewards.get(k) != 1 for k in required):
            raise RuntimeError("Judge result has missing integrity fields")
        if status["status"] == "graded" and (self.task.task_dir / "tests/steps").is_dir():
            from .decisions import validate_step
            validate_step(self.trial_paths.verifier_dir, step, rewards, contract)
        protocol_path = self.trial_paths.trial_dir.parent / "protocol.json"
        protocol = json.loads(protocol_path.read_text()) if protocol_path.is_file() else {}
        if protocol.get("synthetic_fixture"):
            rewards["db_synthetic_fixture"] = 1.
            from .io import atomic_json
            atomic_json(self.trial_paths.verifier_dir / "reward.json", rewards)
        return result
