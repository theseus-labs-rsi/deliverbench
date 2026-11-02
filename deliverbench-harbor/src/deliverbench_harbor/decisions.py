"""Validate the complete published verdict identity set without judging content."""
import json
from pathlib import Path


def validate_unit(unit, expected):
    criteria = unit.get("criteria") or []
    ids = [r.get("rubric_id") for r in criteria]
    if (unit.get("status") != "graded" or len(ids) != len(expected) or
            len(set(ids)) != len(ids) or set(ids) != set(expected) or
            unit.get("rubric_verdict_count") != len(expected) or
            any(r.get("status") != "graded" or type(r.get("hit")) is not bool for r in criteria)):
        raise ValueError("Incomplete, duplicate, mismatched or invalid Judge verdicts")
    return True


def validate_step(folder, step, rewards, contract):
    expected = contract["expected_verdicts"]
    for phase in ["process"] + (["final"] if step == contract["final_step"] else []):
        unit = json.loads((folder / phase / "result.json").read_text())
        validate_unit(unit, expected[step + "/" + phase])
        score_key = "db_final_score" if phase == "final" else "db_score_" + step
        if unit.get("score") != rewards.get(score_key):
            raise ValueError("Score does not match committed Judge decisions")
