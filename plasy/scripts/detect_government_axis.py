#!/usr/bin/env python3
"""Detect the WPCA dimension most associated with Plasy's governing coalition."""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wpca-output", type=Path, default=ROOT / "analyses/wpca/outputs/wpca.json")
    parser.add_argument("--wpca-definition", type=Path, default=ROOT / "analyses/wpca/wpca_definition.json")
    parser.add_argument("--govity-definition", type=Path, default=ROOT / "analyses/govity/govity_definition.json")
    parser.add_argument("--memberships", type=Path, default=ROOT / "data/memberships.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "analyses/wpca/outputs/government_axis.json")
    args = parser.parse_args()

    people = json.loads(args.wpca_output.read_text(encoding="utf-8"))
    wpca_definition = json.loads(args.wpca_definition.read_text(encoding="utf-8"))
    govity_definition = json.loads(args.govity_definition.read_text(encoding="utf-8"))
    government_groups = set(govity_definition["government_groups"])
    with args.memberships.open(encoding="utf-8", newline="") as f:
        government_ids = {
            row["person_id"] for row in csv.DictReader(f)
            if row["organization_id"] in government_groups
        }
    included = [p for p in people if p.get("included", True)]
    n_dims = len(included[0]["dims"]) if included else 0
    correlations = []
    for dim in range(n_dims):
        values = [p["dims"][dim] for p in included]
        labels = [1 if p["person_id"] in government_ids else 0 for p in included]
        mean_x, mean_y = sum(values) / len(values), sum(labels) / len(labels)
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(values, labels)) / len(values)
        var_x = sum((x - mean_x) ** 2 for x in values) / len(values)
        var_y = sum((y - mean_y) ** 2 for y in labels) / len(labels)
        r = cov / math.sqrt(var_x * var_y) if var_x and var_y else 0.0
        correlations.append({"dim_index": dim, "correlation": r})
    if not correlations:
        raise ValueError("WPCA output has no included people/dimensions")
    detected = max(correlations, key=lambda x: abs(x["correlation"]))["dim_index"]
    override = (wpca_definition.get("extras") or {}).get("government_axis_override")
    effective = override if override is not None else detected
    gov_values = [p["dims"][effective] for p in included if p["person_id"] in government_ids]
    opposition_values = [p["dims"][effective] for p in included if p["person_id"] not in government_ids]
    gov_mean = sum(gov_values) / len(gov_values) if gov_values else 0.0
    opposition_mean = sum(opposition_values) / len(opposition_values) if opposition_values else 0.0
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "point_biserial_correlation",
        "n_dims": n_dims,
        "n_included_people": len(included),
        "government_groups": sorted(government_groups),
        "government_person_count": len(gov_values),
        "opposition_person_count": len(opposition_values),
        "correlations": correlations,
        "detected_dim_index": detected,
        "override_dim_index": override,
        "overridden": override is not None,
        "effective_dim_index": effective,
        "government_mean": gov_mean,
        "opposition_mean": opposition_mean,
        "government_sign": 1 if gov_mean >= opposition_mean else -1,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
