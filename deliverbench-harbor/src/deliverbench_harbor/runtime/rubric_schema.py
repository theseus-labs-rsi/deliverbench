#!/usr/bin/env python3
"""Validate DeliverBench Rubric 1.1.0 without contacting a model or changing files."""
import argparse
from collections import Counter
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).with_name("rubric.schema.json")
VERSION = "1.1.0"


class ContractError(ValueError):
    pass


def load_json(path):
    def pairs(values):
        out = {}
        for key, value in values:
            if key in out:
                raise ContractError("Duplicate JSON key: " + key)
            out[key] = value
        return out

    def reject_constant(value):
        raise ContractError("Non-finite JSON number: " + value)

    return json.loads(Path(path).read_text(encoding="utf-8"),
                      object_pairs_hook=pairs, parse_float=Decimal,
                      parse_constant=reject_constant)


def decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ContractError("weight must be a JSON number, not a boolean or string")
    value = Decimal(str(value))
    if not value.is_finite():
        raise ContractError("weight must be finite")
    return value


def integer_weight(value, *, hurdle=False):
    """Explicit authoring conversion; runtime validation never rounds silently."""
    value = decimal(value)
    if type(hurdle) is not bool:
        raise ContractError("hurdle must be boolean")
    if hurdle:
        if value != 0:
            raise ContractError("Hurdle weight must be zero")
        return 0
    if value == 0:
        raise ContractError("Non-hurdle weight must be nonzero")
    rounded = int(value.to_integral_value(rounding=ROUND_FLOOR))
    return max(1, rounded) if value > 0 else rounded


def canonical_path(value, allow_root=False):
    if value == "." and allow_root:
        return
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(c in value for c in "\\:*?\x00\n\r")
            or value.startswith(("/", "~"))
            or any(part in ("", ".", "..") for part in value.split("/"))
            or str(PurePosixPath(value)) != value):
        raise ContractError("Non-canonical relative path: " + repr(value))


def validate(document):
    """Return errors/warnings; structural success is not semantic quality proof."""
    schema = load_json(SCHEMA_PATH.with_name("rubric-1.0.0.schema.json") if isinstance(document, dict) and document.get("schema_version") == "1.0.0" else SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    errors = []
    warnings = []
    # Reject non-finite Python inputs before invoking numeric comparisons.
    if isinstance(document, dict) and isinstance(document.get("rubrics"), list):
        for row in document["rubrics"]:
            if isinstance(row, dict) and "weight" in row:
                try:
                    decimal(row["weight"])
                except ContractError as exc:
                    errors.append(str(row.get("id", "?")) + ": " + str(exc))
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}
    for error in Draft202012Validator(schema).iter_errors(document):
        location = "/" + "/".join(map(str, error.absolute_path))
        errors.append(location + ": " + error.message)
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}

    items = document["rubrics"]
    by_id = {row["id"]: row for row in items}
    duplicates = [rid for rid, count in Counter(row["id"] for row in items).items() if count > 1]
    if duplicates:
        errors.append("Duplicate criterion IDs: " + ", ".join(duplicates))
    if sum((decimal(row["weight"]) for row in items if decimal(row["weight"]) > 0 and not row.get("bonus", False)), Decimal(0)) <= 0:
        errors.append("The evaluation unit must have a positive denominator")

    for row in items:
        rid = row["id"]
        scope = row["artifact_scope"]
        if "." in scope and scope != ["."]:
            errors.append(rid + ": '.' must be the only artifact_scope entry")
        for path in scope:
            try:
                canonical_path(path, allow_root=True)
            except ContractError as exc:
                errors.append(rid + ": " + str(exc))
        for parent in row["depends_on"]:
            if parent == rid or parent not in by_id:
                errors.append(rid + ": self or unknown dependency " + parent)
            elif (
                    decimal(by_id[parent]["weight"]) <= 0 or by_id[parent]["hurdle"]):
                errors.append(rid + ": Dependency parents must be positive non-hurdle criteria")
    # Every dependency is a scoring prerequisite and must form a DAG.
    seen, active = set(), set()

    def visit(rid):
        if rid in active:
            raise ContractError("Dependency cycle at " + rid)
        if rid in seen:
            return
        active.add(rid)
        for parent in by_id[rid]["depends_on"]:
            if parent in by_id:
                visit(parent)
        active.remove(rid)
        seen.add(rid)

    try:
        for rid in by_id:
            visit(rid)
    except (ContractError, RecursionError) as exc:
        errors.append(str(exc) or "Dependency graph exceeds supported depth")

    if document["evaluation_unit"] == "FINAL":
        positive_targets = {row["target"] for row in items if decimal(row["weight"]) > 0 and not row.get("bonus",False)}
        expected = {"final_artifact", "cross_file_relation", "package_delivery"}
        if expected - positive_targets:
            errors.append("FINAL requires positive criteria for A/C/P targets: " + ", ".join(sorted(expected - positive_targets)))

    grouped = {}
    for row in items:
        key = (" ".join(row["description"].split()), row["target"], tuple(sorted(row["artifact_scope"])))
        if key in grouped:
            warnings.append("Review possible duplicate criteria: " + grouped[key] + " / " + row["id"])
        grouped[key] = row["id"]
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def require_valid(document):
    report = validate(document)
    if not report["valid"]:
        raise ContractError("; ".join(report["errors"]))
    return document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    reports = []
    global_ids = {}
    for path in args.files:
        try:
            doc = load_json(path)
            report = validate(doc)
            if report["valid"]:
                for row in doc["rubrics"]:
                    if row["id"] in global_ids:
                        report["errors"].append("Criterion ID also appears in " + global_ids[row["id"]] + ": " + row["id"])
                    global_ids[row["id"]] = str(path)
                report["valid"] = not report["errors"]
            report.update(file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        except (OSError, ValueError) as exc:
            report = {"file": str(path), "valid": False, "errors": [str(exc)], "warnings": []}
        reports.append(report)
    print(json.dumps({"schema_version": VERSION, "valid": all(x["valid"] for x in reports),
                      "files": reports}, ensure_ascii=False, indent=2))
    return 0 if all(x["valid"] for x in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
