"""G4 monotonicity guard: nightly data may only grow.

Compares the freshly regenerated working-tree tables in ``--data-dir`` against the last
git-committed version of the same files (``git show HEAD:<path>``) and fails loudly if:

  1. any row present in the committed version has disappeared from the working-tree version
     (keyed by the table's natural primary key — see ``TABLE_KEYS`` below), or
  2. more than 1% of rows whose key exists in *both* versions have any other field changed.

New rows (key only in the working-tree version) are expected growth and never fail the gate.

Design notes:
  - City-agnostic on purpose (plan.md G4: "Brno/Ostrava will need this too") — takes `--data-dir`
    like `validate_tables.py`/`validate_records.py`, no city-specific imports.
  - Runs against `git show HEAD:<path>`, i.e. the last *committed* state, not the previous
    in-memory value — this is deliberate: it's meant to run in CI after standardize.py +
    party_affiliation.py have regenerated the working tree but *before* `git add`/`git commit`,
    so "old" always means "what's live on the branch right now" and "new" means "what this run
    just produced." Running it against an uncommitted repo (nothing at HEAD for a path yet, e.g.
    first-ever run for a new city) is treated as "no prior state" — nothing to compare, gate
    passes trivially and logs that fact rather than failing.
  - `votes.csv` has no single-column `id`; its natural key is the (vote_event_id, voter_id) pair
    (one row per person per vote event) — see TABLE_KEYS.
  - Only compares files that are actually tracked by the shared table list; a city missing one of
    these files (e.g. no motions yet) is skipped with a log line, not a hard failure, mirroring
    how validate_tables.py/validate_records.py skip missing files.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]

# filename -> (key columns forming the natural primary key, i.e. the tuple of column names whose
# combined value must not repeat and must not disappear across runs)
TABLE_KEYS: dict[str, tuple[str, ...]] = {
    "persons.csv": ("id",),
    "organizations.csv": ("id",),
    "memberships.csv": ("id",),
    "votes.csv": ("vote_event_id", "voter_id"),
    "vote_events.json": ("id",),
    "motions.json": ("id",),
}

# Fraction of matched-key rows allowed to change value before the gate fails (plan.md G4: ">1%").
_CHANGE_THRESHOLD = 0.01


class MonotonicityViolation(RuntimeError):
    """Raised when G4 detects disappeared rows or excessive changes."""


def _git_show(path_in_repo: str) -> str | None:
    """Return the content of `path_in_repo` at HEAD, or None if it doesn't exist at HEAD."""
    result = subprocess.run(
        ["git", "show", f"HEAD:{path_in_repo}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Distinguish "path did not exist at HEAD" (expected for a brand-new table) from other
        # git errors, which should not be silently swallowed.
        stderr = result.stderr.strip()
        if "does not exist" in stderr or "exists on disk, but not in" in stderr or "fatal: path" in stderr:
            return None
        raise RuntimeError(f"git show HEAD:{path_in_repo} failed unexpectedly: {stderr}")
    return result.stdout


def _rows_from_csv_text(text: str) -> list[dict[str, Any]]:
    return list(csv.DictReader(text.splitlines()))


def _rows_from_csv_file(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _rows_from_json_text(text: str) -> list[dict[str, Any]]:
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("expected a JSON array of records")
    return data


def _rows_from_json_file(path: Path) -> list[dict[str, Any]]:
    return _rows_from_json_text(path.read_text(encoding="utf-8"))


def _key_of(row: dict[str, Any], key_columns: tuple[str, ...]) -> tuple[Any, ...]:
    return tuple(row.get(c) for c in key_columns)


def compare_table(
    filename: str,
    key_columns: tuple[str, ...],
    old_rows: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare old (committed) vs. new (working-tree) rows for one table.

    Returns a report dict; raises MonotonicityViolation if the gate is tripped.
    """
    old_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in old_rows:
        key = _key_of(row, key_columns)
        old_by_key[key] = row

    new_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in new_rows:
        key = _key_of(row, key_columns)
        new_by_key[key] = row

    old_keys = set(old_by_key)
    new_keys = set(new_by_key)

    disappeared = sorted(old_keys - new_keys, key=repr)
    added = new_keys - old_keys
    common = old_keys & new_keys

    changed: list[dict[str, Any]] = []
    for key in common:
        old_row = old_by_key[key]
        new_row = new_by_key[key]
        # Compare on the union of columns present in either version; a column that appears only
        # in one is itself a "change" (schema drift within a row), not ignored.
        cols = set(old_row) | set(new_row)
        diffs = {c: (old_row.get(c), new_row.get(c)) for c in cols if old_row.get(c) != new_row.get(c)}
        if diffs:
            changed.append({"key": key, "diffs": diffs})

    change_rate = (len(changed) / len(common)) if common else 0.0

    report = {
        "table": filename,
        "old_row_count": len(old_rows),
        "new_row_count": len(new_rows),
        "disappeared_count": len(disappeared),
        "disappeared_keys": disappeared,
        "added_count": len(added),
        "matched_count": len(common),
        "changed_count": len(changed),
        "change_rate": change_rate,
        "changed_rows": changed,
    }

    if disappeared:
        logging.error(
            "%s: %d row(s) present at HEAD are MISSING from the new version: %s",
            filename,
            len(disappeared),
            disappeared[:20],
        )
        raise MonotonicityViolation(
            f"{filename}: {len(disappeared)} row(s) disappeared (keys: {disappeared[:20]}"
            f"{'...' if len(disappeared) > 20 else ''})"
        )

    if change_rate > _CHANGE_THRESHOLD:
        logging.error(
            "%s: %d/%d matched rows changed (%.2f%%), exceeds the %.0f%% threshold. First offenders: %s",
            filename,
            len(changed),
            len(common),
            change_rate * 100,
            _CHANGE_THRESHOLD * 100,
            changed[:10],
        )
        raise MonotonicityViolation(
            f"{filename}: {len(changed)}/{len(common)} matched rows changed "
            f"({change_rate * 100:.2f}% > {_CHANGE_THRESHOLD * 100:.0f}% threshold)"
        )

    logging.info(
        "%s: OK — %d old, %d new, %d added, %d matched, %d changed (%.3f%%), 0 disappeared",
        filename,
        len(old_rows),
        len(new_rows),
        len(added),
        len(common),
        len(changed),
        change_rate * 100,
    )
    return report


def run(data_dir: Path) -> list[dict[str, Any]]:
    """Run the G4 guard over every table in TABLE_KEYS found under `data_dir`.

    `data_dir` is expected to be a path like `praha/data` relative to the repo root (or an
    absolute path inside the repo) — the git-relative path is derived from it so `git show
    HEAD:<path>` resolves correctly regardless of cwd.
    """
    data_dir = data_dir.resolve()
    try:
        rel_data_dir = data_dir.relative_to(_REPO_ROOT)
    except ValueError as exc:
        raise ValueError(f"--data-dir {data_dir} is not inside the repo root {_REPO_ROOT}") from exc

    reports = []
    for filename, key_columns in TABLE_KEYS.items():
        new_path = data_dir / filename
        if not new_path.exists():
            logging.info("Skipping %s (not present in %s)", filename, data_dir)
            continue

        rel_path = str(rel_data_dir / filename)
        old_text = _git_show(rel_path)

        is_json = filename.endswith(".json")
        new_rows = _rows_from_json_file(new_path) if is_json else _rows_from_csv_file(new_path)

        if old_text is None:
            logging.info(
                "%s: no committed version at HEAD (new table) — nothing to compare, gate passes trivially.",
                filename,
            )
            reports.append(
                {
                    "table": filename,
                    "old_row_count": 0,
                    "new_row_count": len(new_rows),
                    "disappeared_count": 0,
                    "note": "no prior committed version",
                }
            )
            continue

        old_rows = _rows_from_json_text(old_text) if is_json else _rows_from_csv_text(old_text)
        reports.append(compare_table(filename, key_columns, old_rows, new_rows))

    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", required=True, help="e.g. praha/data")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    try:
        reports = run(Path(args.data_dir))
    except MonotonicityViolation as exc:
        logging.error("G4 monotonicity guard FAILED: %s", exc)
        raise SystemExit(1) from exc

    logging.info("G4 monotonicity guard passed for %d table(s).", len(reports))


if __name__ == "__main__":
    main()
