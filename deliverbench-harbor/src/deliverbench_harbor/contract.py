"""Resolve a task-owned protocol into an immutable, per-trial acceptance contract."""
import hashlib
import json
import re
from pathlib import Path

from . import __version__

PROTOCOL = "deliverbench-harbor-v1"


def read(path):
    return json.loads(Path(path).read_text())


def project_background(root, config):
    """Compose public kickoff files in declared order without rewriting text."""
    kickoff = config.get("kickoff", {"instruction": "instruction.md", "timeout_sec": 180})
    if (not isinstance(kickoff, dict)
            or set(kickoff) not in ({"instruction", "timeout_sec"}, {"instructions", "timeout_sec"})
            or type(kickoff.get("timeout_sec")) not in (int, float)
            or not 0 < kickoff["timeout_sec"] <= 600):
        raise ValueError("Invalid unscored kickoff configuration")
    names = kickoff.get("instructions", [kickoff.get("instruction")])
    if (not isinstance(names, list) or not names
            or any(not isinstance(n, str) for n in names)
            or len(set(names)) != len(names) or names[0] != "instruction.md"):
        raise ValueError("Kickoff files must be a nonempty ordered unique list")
    from .runtime.rubric_schema import canonical_path
    parts = []
    for name in names:
        canonical_path(name)
        path = root
        for part in Path(name).parts:
            path /= part
            if path.is_symlink():
                raise ValueError("Kickoff source cannot be a symlink")
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("Kickoff source must be inside the task")
        if name != "instruction.md":
            allowed = [root/"environment/data/source", root/"steps"/config["steps"][0]/"workdir/release_materials"]
            if not any(path.is_relative_to(folder) for folder in allowed):
                raise ValueError("Kickoff may include only initially released public sources")
        text = path.read_text()
        if not text.strip():
            raise ValueError("Empty kickoff source")
        parts.append({"path": name, "text": text, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    text = "\n\n".join(part["text"] for part in parts)
    return {"text": text, "parts": parts, "sha256": hashlib.sha256(text.encode()).hexdigest()}


def task_prompt(background, stage, instruction, *, final=False):
    if final:
        return ('# 项目背景\n\n' + background + '\n\n# 最终评审委托\n\n' + instruction
                + '\n评审 submission/ 中真实最终交付，全部阶段委托位于 instructions/。来源绑定见 evaluation_manifest.json；来源阶段对应文件按绑定读取当前最终版本，历史材料只供核验，不能补交。本次使用独立 FINAL 判据，不叠加来源阶段得分。\n')
    return ('# 项目背景\n\n' + background + '\n\n# 本次阶段委托：' + stage + '\n\n'
            + instruction + '\n前序委托见 output/instructions/；当前被评文件见 output/submission/。\n')


def validate_snapshot(snapshot):
    steps = snapshot.get("steps")
    if (snapshot.get("schema_version") not in (1, 2) or not isinstance(steps, list) or not steps
            or any(not isinstance(s, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", s) for s in steps)
            or len(set(steps)) != len(steps) or snapshot.get("final_step") != steps[-1]):
        raise ValueError("Invalid DeliverBench stage contract")
    expected = snapshot.get("expected_verdicts", {})
    required = {s + "/process" for s in steps} | {steps[-1] + "/final"}
    if set(expected) != required:
        raise ValueError("Verdict phases must match the declared stages")
    for ids in expected.values():
        if (not isinstance(ids, list) or not ids or
                any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids)):
            raise ValueError("Expected verdict IDs must be nonempty and unique per phase")
    return snapshot


def load_task(task_dir):
    from harbor.models.task.task import Task
    root = Path(task_dir).resolve()
    source = root / "deliverbench.json"
    config = read(source)
    if config.get("schema_version") not in (1, 2):
        raise ValueError("Unsupported DeliverBench task schema")
    if config.get("extension_version") != __version__ or config.get("harbor_version") != "0.20.0":
        raise ValueError("Task requires a different DeliverBench/Harbor version")
    if config.get("scoring") != "flat_integer" or config.get("tests_dir") != "tests":
        raise ValueError("Unsupported scoring or tests layout")
    if config.get("continuous_conversation") is not True:
        raise ValueError("Protocol v1 requires native continuous conversation")
    steps = config.get("steps")
    if steps != [s.name for s in Task(root).config.steps]:
        raise ValueError("DeliverBench stages must exactly match native task.toml stages")
    metric_path = (root / config["metric_contract"]).resolve()
    if not metric_path.is_relative_to(root.parent) or not metric_path.is_file():
        raise ValueError("Metric contract must be inside the project package")
    metric = read(metric_path)
    if metric.get("steps") != steps or metric.get("scoring") != "flat_integer":
        raise ValueError("Metric and task stage/scoring contracts disagree")
    if config.get("rubric_schema_version") not in ("1.0.0", "1.1.0") or config.get("evidence_view") != "cumulative_raw":
        raise ValueError("This extension requires supported Rubric schema and cumulative_raw evidence")
    from .runtime.rubric_schema import load_json, require_valid
    # Derive the authoritative release inventory from the actual public package.
    release_inventory = {}
    roots = [(steps[0], root / "environment/data/source")] + [
        (stage, root / "steps" / stage / "workdir/release_materials") for stage in steps]
    for stage, folder in roots:
        for path in folder.rglob("*"):
            if path.is_symlink():
                raise ValueError("Released input must not be a symlink")
            if path.is_file():
                name = path.relative_to(folder).as_posix()
                value = {"visible_from": stage, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                if name in release_inventory and release_inventory[name]["sha256"] != value["sha256"]:
                    raise ValueError("Released input cannot be overwritten")
                release_inventory.setdefault(name, value)
    expected = {}
    from .responses import validate_config
    response_events = validate_config(root, steps, release_inventory)
    if bool(response_events) != (config.get("schema_version") == 2):
        raise ValueError("Dynamic responses require task contract schema 2")
    tracked = [source, root / "task.toml", metric_path]
    for step in steps:
        folder = root / "tests/steps" / step
        runtime = read(folder / "runtime_contract.json")
        if runtime.get("response_events", []) != response_events:
            raise ValueError("Runtime dynamic response contract mismatch")
        if runtime.get("steps") != steps or runtime.get("current_step") != step:
            raise ValueError("Stage runtime contract disagrees with task")
        if (runtime.get("rubric_schema_version") != config["rubric_schema_version"] or runtime.get("evidence_view") != "cumulative_raw"
                or set(runtime.get("stage_instructions", {})) != set(steps[:steps.index(step)+1])
                or runtime.get("project_instruction") != project_background(root, config)["text"]):
            raise ValueError("Runtime schema, evidence or instruction contract mismatch")
        for stage, text in runtime["stage_instructions"].items():
            if text != (root/"steps"/stage/"instruction.md").read_text():
                raise ValueError("Candidate/Judge stage instruction mismatch")
        if config["rubric_schema_version"] == "1.1.0":
            if (root/"environment/data/instruction.md").read_bytes() != (root/"instruction.md").read_bytes():
                raise ValueError("Workspace project instruction mismatch")
            for final in ([False, True] if step == config["final_step"] else [False]):
                expected_prompt = task_prompt(runtime["project_instruction"], step,
                    runtime["stage_instructions"][step], final=final)
                if (folder/("final_task_prompt.md" if final else "task_prompt.md")).read_text() != expected_prompt:
                    raise ValueError("Judge delegation differs from canonical instructions")
        material_rows = runtime.get("input_files", [])
        material_map = {r["candidate_path"]:{k:r[k] for k in ("visible_from","sha256")} for r in material_rows}
        if len(material_map) != len(material_rows) or material_map != release_inventory:
            raise ValueError("Source catalog must match actual material releases and hashes")
        if set(runtime.get("outputs", {})) != set(steps):
            raise ValueError("Stage outputs must declare every configured stage")
        for phase, name in [("process", "rubric.json")] + (
                [("final", "final_rubric.json")] if step == config["final_step"] else []):
            path = folder / name
            document = require_valid(load_json(path))
            if document["schema_version"] != config["rubric_schema_version"]:
                raise ValueError("Rubric schema version mismatch")
            if document["evaluation_unit"] != ("FINAL" if phase == "final" else step):
                raise ValueError("Rubric evaluation unit mismatch")
            denominator = sum(r["weight"] for r in document["rubrics"] if r["weight"] > 0 and not r.get("bonus", False))
            if denominator != runtime.get("final_positive_denominator" if phase == "final" else "positive_denominator"):
                raise ValueError("Rubric denominator mismatch")
            if phase == "final" and denominator != metric["positive_denominator"]:
                raise ValueError("Final metric denominator mismatch")
            if phase == "final":
                mapping = runtime.get("final_rubric_sources", {})
                if set(mapping) != {r["id"] for r in document["rubrics"]} or any(v not in [*steps,"FINAL"] for v in mapping.values()):
                    raise ValueError("Final source binding must cover every criterion")
            expected[step + "/" + phase] = [r["id"] for r in document["rubrics"]]
            groups=config.get("incompatible_hits",{}).get("FINAL" if phase=="final" else step,[])
            if runtime.get("final_incompatible_hits" if phase=="final" else "incompatible_hits",[])!=groups:
                raise ValueError("Incompatible-criterion rule mismatch")
            for group in groups:
                if (not isinstance(group,list) or len(group)<2 or len(group)!=len(set(group))
                        or not set(group)<=set(expected[step+"/"+phase])):
                    raise ValueError("Incompatible hits must refer to this scoring unit")
        request = read(root / "steps" / step / "workdir/deliverbench_stage.json")
        if request != {"step": step}:
            raise ValueError("Stage release request does not match its native step")
        tracked.extend(p for p in folder.rglob("*") if p.is_file())
    tracked.extend(p for p in (root / "steps").rglob("*")
                   if p.is_file())
    tracked.extend(p for p in (root / "environment/data").rglob("*") if p.is_file())
    tracked.append(root / "tests/judge_instruction.md")
    tracked.extend(p for p in (root / "responses").rglob("*") if p.is_file())
    for folder in ("environment", "solution", "tests"):
        tracked.extend(p for p in (root/folder).rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc")
    snapshot = {**config, "expected_verdicts": expected}
    snapshot["response_events"] = response_events
    if "kickoff" in config:
        background = project_background(root, config)
        tracked.extend(root / part["path"] for part in background["parts"])
        snapshot["kickoff"] = {**config["kickoff"], **background, "language": config.get("language", "zh")}
    snapshot["definition_sha256"] = {
        str(p.relative_to(root)) if p.is_relative_to(root) else str(Path("..") / p.relative_to(root.parent)):
        hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(tracked))
    }
    snapshot["protocol"] = PROTOCOL
    extension_root=Path(__file__).parent
    snapshot["extension_sha256"]={p.relative_to(extension_root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in sorted(extension_root.rglob("*"))
                                  if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc"}
    validate_snapshot(snapshot)
    snapshot["contract_sha256"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return snapshot


def trial_contract(trial):
    snapshot = read(Path(trial) / "deliverbench_contract.json")
    if snapshot.get("protocol") != PROTOCOL:
        raise ValueError("Missing or unsupported per-trial protocol")
    digest = snapshot.get("contract_sha256")
    payload = {k: v for k, v in snapshot.items() if k != "contract_sha256"}
    if digest != hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest():
        raise ValueError("Trial contract digest mismatch")
    return validate_snapshot(snapshot)
