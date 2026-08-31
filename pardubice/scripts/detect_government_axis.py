"""Detect which WPCA dimension is the government/opposition axis for Zastupitelstvo města Pardubice.

Mirrors praha/scripts/detect_government_axis.py (point-biserial correlation between each
dimension's per-person values and government membership, derived from
`pardubice/analyses/govity/govity_definition.json`'s `government_groups` expanded via
`pardubice/data/memberships.csv`). Ported from Hradec Králové's copy, so it also unions in
`government_members` — but Pardubice's D7-approved govity_definition.json has
`government_members: []` (the ANO 2011 + ŽP + SpP coalition is fully covered by whole-klub
`government_groups`, no named individual defectors), so that code path is dormant here. Kept as-is
for consistency with the other cities' copies rather than trimmed.

Output: a sidecar JSON, NOT a change to wpca.json itself. Written to
`pardubice/analyses/wpca/outputs/government_axis.json`.

Usage:
  python pardubice/scripts/detect_government_axis.py
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_CITY_ROOT = Path(__file__).resolve().parents[1]

_WPCA_OUTPUT = _CITY_ROOT / "analyses" / "wpca" / "outputs" / "wpca.json"
_WPCA_DEFINITION = _CITY_ROOT / "analyses" / "wpca" / "wpca_definition.json"
_GOVITY_DEFINITION = _CITY_ROOT / "analyses" / "govity" / "govity_definition.json"
_MEMBERSHIPS = _CITY_ROOT / "data" / "memberships.csv"
_OUTPUT = _CITY_ROOT / "analyses" / "wpca" / "outputs" / "government_axis.json"


def _government_person_ids(
    memberships_path: Path, government_groups: list[str], government_members: list[str]
) -> set[str]:
    government_orgs = set(government_groups)
    person_ids: set[str] = set(government_members)
    with open(memberships_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["organization_id"] in government_orgs:
                person_ids.add(row["person_id"])
    return person_ids


def _point_biserial(values: list[float], labels: list[int]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    mean_x = sum(values) / n
    mean_y = sum(labels) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(values, labels)) / n
    var_x = sum((x - mean_x) ** 2 for x in values) / n
    var_y = sum((y - mean_y) ** 2 for y in labels) / n
    if var_x == 0 or var_y == 0:
        return 0.0
    return cov / (var_x**0.5 * var_y**0.5)


def detect_government_axis(
    *,
    wpca_output_path: Path = _WPCA_OUTPUT,
    wpca_definition_path: Path = _WPCA_DEFINITION,
    govity_definition_path: Path = _GOVITY_DEFINITION,
    memberships_path: Path = _MEMBERSHIPS,
) -> dict:
    wpca_people = json.loads(wpca_output_path.read_text(encoding="utf-8"))
    wpca_definition = json.loads(wpca_definition_path.read_text(encoding="utf-8"))
    govity_definition = json.loads(govity_definition_path.read_text(encoding="utf-8"))

    government_groups = govity_definition["government_groups"]
    government_members = govity_definition.get("government_members") or []
    government_ids = _government_person_ids(memberships_path, government_groups, government_members)

    included = [p for p in wpca_people if p.get("included", True)]
    n_dims = len(included[0]["dims"]) if included else 0

    correlations = []
    for dim_index in range(n_dims):
        values = [p["dims"][dim_index] for p in included]
        labels = [1 if p["person_id"] in government_ids else 0 for p in included]
        r = _point_biserial(values, labels)
        correlations.append({"dim_index": dim_index, "correlation": r})

    detected_dim_index = max(correlations, key=lambda c: abs(c["correlation"]))["dim_index"]

    override = (wpca_definition.get("extras") or {}).get("government_axis_override")
    overridden = override is not None
    effective_dim_index = override if overridden else detected_dim_index

    gov_values = [p["dims"][effective_dim_index] for p in included if p["person_id"] in government_ids]
    opp_values = [p["dims"][effective_dim_index] for p in included if p["person_id"] not in government_ids]
    government_mean = sum(gov_values) / len(gov_values) if gov_values else 0.0
    opposition_mean = sum(opp_values) / len(opp_values) if opp_values else 0.0
    government_sign = 1 if government_mean >= opposition_mean else -1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "point_biserial_correlation",
        "n_dims": n_dims,
        "n_included_people": len(included),
        "government_groups": government_groups,
        "government_members": government_members,
        "government_person_count": len(gov_values),
        "opposition_person_count": len(opp_values),
        "correlations": correlations,
        "detected_dim_index": detected_dim_index,
        "override_dim_index": override,
        "overridden": overridden,
        "effective_dim_index": effective_dim_index,
        "government_mean": government_mean,
        "opposition_mean": opposition_mean,
        "government_sign": government_sign,
        "sources": {
            "wpca_output": str(wpca_output_path.relative_to(_CITY_ROOT.parent)),
            "wpca_definition": str(wpca_definition_path.relative_to(_CITY_ROOT.parent)),
            "govity_definition": str(govity_definition_path.relative_to(_CITY_ROOT.parent)),
            "memberships": str(memberships_path.relative_to(_CITY_ROOT.parent)),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wpca-output", default=str(_WPCA_OUTPUT), dest="wpca_output")
    parser.add_argument("--wpca-definition", default=str(_WPCA_DEFINITION), dest="wpca_definition")
    parser.add_argument("--govity-definition", default=str(_GOVITY_DEFINITION), dest="govity_definition")
    parser.add_argument("--memberships", default=str(_MEMBERSHIPS))
    parser.add_argument("--output", default=str(_OUTPUT))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    result = detect_government_axis(
        wpca_output_path=Path(args.wpca_output),
        wpca_definition_path=Path(args.wpca_definition),
        govity_definition_path=Path(args.govity_definition),
        memberships_path=Path(args.memberships),
    )

    for c in result["correlations"]:
        logging.info("dim_index=%d correlation=%.4f", c["dim_index"], c["correlation"])
    logging.info(
        "detected_dim_index=%d overridden=%s effective_dim_index=%d government_sign=%+d",
        result["detected_dim_index"],
        result["overridden"],
        result["effective_dim_index"],
        result["government_sign"],
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    logging.info("Wrote %s", output_path)


if __name__ == "__main__":
    main()
