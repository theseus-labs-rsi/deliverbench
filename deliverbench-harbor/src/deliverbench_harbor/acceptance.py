"""Read-only job acceptance. Exit success means complete, never just score > 0."""
import argparse
import json
from pathlib import Path
from .io import inventory

from .contract import PROTOCOL, trial_contract


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def summarize_trial(result, path):
    contract = trial_contract(path)
    required_steps = contract["steps"]
    steps = result.get("step_results") or []
    rows = []
    for step in steps:
        name = step.get("step_name")
        rewards = (step.get("verifier_result") or {}).get("rewards") or {}
        probe = rewards.get("db_non_model_probe") == 1
        receipt = read(path / "evidence/candidate" / str(name) /
                       "workspace/.checkpoints" / (str(name) + "_deliverbench_output.json"), {})
        candidate = "succeeded" if receipt.get("candidate_completed") is True else (
            "failed" if step.get("exception_info") else "unknown")
        judge = "infra_probe" if probe else (
            "graded" if rewards.get("db_graded_" + str(name)) == 1 else "incomplete")
        rows.append({"step": name, "candidate_status": candidate, "judge_status": judge,
                     "score": rewards.get("db_score_" + str(name)) if judge == "graded" else None,
                     "exception_type": (step.get("exception_info") or {}).get("exception_type")})
    storage = read(path / "evidence/storage_status.json", {})
    cancelled = (path / "cancellation.json").exists() or (
        result.get("exception_info") or {}).get("exception_type") == "CancelledError"
    from .kickoff import validate as validate_kickoff
    try:
        kickoff_status = validate_kickoff(path, contract,
            oracle=(result.get("agent_info") or {}).get("name") == "oracle")
        kickoff_ok = True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        kickoff_status, kickoff_ok = "incomplete", False
    pipeline = (kickoff_ok and [r["step"] for r in rows] == required_steps and
                all(r["candidate_status"] == "succeeded" for r in rows) and
                not result.get("exception_info") and not any(s.get("exception_info") for s in steps) and not cancelled and
                storage.get("status") != "storage_incomplete")
    scored = pipeline and all(r["judge_status"] == "graded" for r in rows)
    last = ((steps[-1].get("verifier_result") or {}).get("rewards") or {}) if steps else {}
    scored = scored and last.get("db_final_graded") == 1
    status = ("cancelled" if cancelled else "storage_incomplete" if storage else
              "scored" if scored else "infra_probe" if pipeline and all(
                  r["judge_status"] == "infra_probe" for r in rows) else "incomplete")
    synthetic = read(path.parent / "protocol.json", {}).get("synthetic_fixture") is True
    reference = any(((s.get("verifier_result") or {}).get("rewards") or {}).get("db_reference_run") == 1 for s in steps)
    actual = scored and not synthetic and not reference
    return {"status": status, "pipeline_complete": pipeline, "scoring_complete": scored,
            "actual_model_evaluation": actual, "reference_run": reference, "synthetic_fixture": synthetic,
            "final_score": last.get("db_final_score") if actual else None,
            "kickoff_status": kickoff_status, "steps": rows, "trial_exception": (result.get("exception_info") or {}).get("exception_type")}


def check_job(root, *, verify_evidence=False):
    root = Path(root)
    protocol_record = read(root / "protocol.json", {})
    synthetic = protocol_record.get("synthetic_fixture") is True
    trials = []
    for path in sorted(root.glob("*/result.json")):
        result = read(path)
        if not isinstance(result, dict) or "step_results" not in result:
            continue
        try:
            contract = trial_contract(path.parent)
            record = summarize_trial(result, path.parent)
        except (OSError, ValueError, KeyError, TypeError):
            trials.append({"trial": path.parent.name, "status": "invalid_contract",
                           "scoring_complete": False, "evidence_complete": False})
            continue
        record["trial"] = path.parent.name
        manifests = list((path.parent / "evidence/candidate").glob("*/manifest.json"))
        required = {s["step_name"] for s in result.get("step_results") or []} | {"at_stop"}
        evidence_ok = required <= {m.parent.name for m in manifests} and all(
            read(m, {}).get("status") == "committed" and read(m, {}).get("role") == "candidate"
            for m in manifests)
        if verify_evidence:
            evidence_ok = evidence_ok and all(
                read(m, {}).get("files") == inventory(m.parent / "workspace") and
                ("logs" not in read(m, {}) or read(m, {})["logs"] == inventory(m.parent / "agent")) and
                ("native_logs" not in read(m, {}) or read(m, {})["native_logs"] == inventory(m.parent / "native"))
                for m in manifests)
        record["evidence_complete"] = evidence_ok
        if record["scoring_complete"] and not synthetic:
            from .decisions import validate_step
            try:
                for step in result["step_results"]:
                    name = step["step_name"]
                    validate_step(path.parent / "steps" / name / "verifier", name,
                                  step["verifier_result"]["rewards"], contract)
                record["verdicts_complete"] = True
            except (OSError, ValueError, KeyError, TypeError):
                record["verdicts_complete"] = False
                record["scoring_complete"] = False
                record["status"] = "invalid_verdicts"
                record["final_score"] = None
        if record["scoring_complete"] and (result.get("agent_info") or {}).get("name") == "codex":
            sessions = []
            for step in contract["steps"]:
                ids = set()
                trace = path.parent / "steps" / step / "agent/codex.txt"
                if trace.is_file():
                    for line in trace.read_text(errors="replace").splitlines():
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") == "thread.started":
                            ids.add(event.get("thread_id"))
                sessions.append(ids)
            continuous = all(len(s) == 1 and None not in s for s in sessions) and len(set.union(*sessions)) == 1
            record["native_continuity"] = continuous
            if not continuous:
                record["scoring_complete"] = False
                record["status"] = "conversation_discontinuous"
                record["final_score"] = None
        record["synthetic_fixture"] = synthetic
        trials.append(record)
    protocol = protocol_record.get("protocol") == PROTOCOL
    return {"protocol_loaded": protocol, "trials": trials,
            "complete": bool(trials) and protocol and all(
                t["scoring_complete"] and t["evidence_complete"] for t in trials),
            "probe_complete": bool(trials) and protocol and all(
                t["status"] == "infra_probe" and t["evidence_complete"] for t in trials)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--allow-infra-probe", action="store_true")
    parser.add_argument("--verify-evidence", action="store_true")
    args = parser.parse_args()
    result = check_job(args.job, verify_evidence=args.verify_evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["complete"] or (args.allow_infra_probe and result["probe_complete"]) else 1)


if __name__ == "__main__":
    main()
