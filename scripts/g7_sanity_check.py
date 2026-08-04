"""G7 range sanity: spot-check an analysis output JSON before it gets committed.

Per plan.md G7: "analysis output spot-checked by script: shares in [0,1], attendance denominator =
events in membership window, wpca dims finite (no NaN — see audit T4)."

This script implements the two checks that generalize across all four analyses (no-NaN/Infinity,
and share-like fields in [0,1]); it does NOT implement the attendance-denominator check because
that needs attendance-specific knowledge (vote_events.json + memberships.csv) beyond a single
output file — see the module docstring note at the bottom for why that's out of scope here and
where it would go if built.

City-agnostic and analysis-agnostic on purpose (plan.md G7, mirrors G4): takes `--output-file`
pointing at any `<slug>.json` analysis output, no city- or slug-specific imports.

Two checks:
  1. No NaN/Infinity anywhere. Python's `json` module *accepts* bare `NaN`/`Infinity`/`-Infinity`
     tokens on load by default (`parse_constant`), even though they are not valid per the JSON
     spec — so a `json.load()` round-trip alone would silently let them through. This script
     therefore (a) scans the raw file text for those literal tokens directly, and (b) after
     parsing, recursively walks every float in the structure and checks `math.isnan`/`math.isinf`
     as a second, independent guard (catches the case where a value was written as a huge/odd
     float that satisfies neither check alone, and gives a JSON-pointer-style path to the exact
     offending field either way).
  2. Any field that looks like a share/rate/percentage — heuristic: the key name contains "share",
     "rate", "pct", or "percent" (case-insensitive) — must be a number in [0, 1]. This is a
     smoke-test heuristic, not a schema: it does not attempt to catch every possible fraction field
     by value alone (arbitrary floats between 0 and 1 are common and not inherently share-like), and
     it deliberately ignores non-numeric or null values for such keys (upstream schema validation,
     G1, is responsible for type-correctness; this gate is about range sanity on the numbers that
     are present).
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
from pathlib import Path
from typing import Any

_BARE_NAN_INF_RE = re.compile(r"(?<![\w.])(-?Infinity|NaN)(?![\w])")

_SHARE_LIKE_KEY_RE = re.compile(r"(share|rate|pct|percent)", re.IGNORECASE)


class RangeSanityViolation(RuntimeError):
    """Raised when G7 detects a NaN/Infinity value or an out-of-range share-like field."""


def _path_str(path: list[Any]) -> str:
    """Render a walk path like ['rows', 3, 'present_share'] as 'rows[3].present_share'."""
    out = ""
    for p in path:
        if isinstance(p, int):
            out += f"[{p}]"
        else:
            out += f".{p}" if out else str(p)
    return out or "$"


def check_raw_text_for_bare_nan_inf(text: str) -> list[str]:
    """Scan the raw JSON text for literal NaN/Infinity/-Infinity tokens (invalid JSON that
    Python's json module would otherwise accept silently). Returns a list of matched tokens with
    surrounding context, empty if none found."""
    hits = []
    for m in _BARE_NAN_INF_RE.finditer(text):
        start = max(0, m.start() - 40)
        end = min(len(text), m.end() + 10)
        hits.append(f"{m.group(1)!r} near: ...{text[start:end]}...")
    return hits


def walk_for_non_finite(obj: Any, path: list[Any] | None = None) -> list[str]:
    """Recursively walk a parsed JSON structure and report every non-finite float found."""
    path = path if path is not None else []
    problems: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            problems += walk_for_non_finite(v, path + [k])
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            problems += walk_for_non_finite(v, path + [i])
    elif isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            problems.append(f"{_path_str(path)} = {obj!r}")
    return problems


def walk_for_out_of_range_shares(obj: Any, path: list[Any] | None = None) -> list[str]:
    """Recursively walk a parsed JSON structure; for any dict key matching the share-like
    heuristic whose value is a bool-excluded int/float, flag it if outside [0, 1]."""
    path = path if path is not None else []
    problems: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SHARE_LIKE_KEY_RE.search(k) and isinstance(v, (int, float)) and not isinstance(v, bool):
                if not (0 <= v <= 1):
                    problems.append(f"{_path_str(path + [k])} = {v!r} (expected in [0, 1])")
            problems += walk_for_out_of_range_shares(v, path + [k])
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            problems += walk_for_out_of_range_shares(v, path + [i])
    return problems


def check_output_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")

    bare_hits = check_raw_text_for_bare_nan_inf(text)
    data = json.loads(text)  # Python's json accepts NaN/Infinity by default; caught above already.
    non_finite = walk_for_non_finite(data)
    out_of_range = walk_for_out_of_range_shares(data)

    report = {
        "file": str(path),
        "bare_nan_inf_tokens": bare_hits,
        "non_finite_values": non_finite,
        "out_of_range_shares": out_of_range,
    }

    problems = []
    if bare_hits:
        problems.append(f"{len(bare_hits)} bare NaN/Infinity token(s) in raw JSON text: {bare_hits[:10]}")
    if non_finite:
        problems.append(f"{len(non_finite)} non-finite float value(s): {non_finite[:10]}")
    if out_of_range:
        problems.append(f"{len(out_of_range)} out-of-range share-like field(s): {out_of_range[:10]}")

    if problems:
        for p in problems:
            logging.error("G7 violation in %s: %s", path, p)
        raise RangeSanityViolation(f"{path}: " + "; ".join(problems))

    logging.info("%s: OK — no NaN/Infinity, no out-of-range share-like fields.", path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-file", required=True, help="path to an analysis output JSON, e.g. praha/analyses/attendance/outputs/attendance.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        check_output_file(Path(args.output_file))
    except RangeSanityViolation as exc:
        logging.error("G7 range sanity check FAILED: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

# Note on the attendance-denominator check (plan.md G7's third bullet: "attendance denominator =
# events in membership window"): that check needs cross-referencing an attendance output row's
# `vote_events_total` against an independent recount of vote_events.json filtered to the person's
# membership interval from memberships.csv — i.e. two extra input files beyond the single
# `--output-file` this script takes, and attendance-specific field names. Deliberately not built
# here to keep this gate generic across all four analyses (as the plan asks); if/when it's wanted,
# it belongs as a small attendance-specific script (e.g. scripts/g7_attendance_denominator.py) that
# takes --output/--vote-events/--memberships, reusing this module's RangeSanityViolation pattern.
