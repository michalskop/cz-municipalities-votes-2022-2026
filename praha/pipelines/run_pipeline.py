"""Praha pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check (G2).

Usage:
    python praha/pipelines/run_pipeline.py
    python praha/pipelines/run_pipeline.py --skip-download   # reuse the last downloaded CSV

Exits non-zero if any stage fails: a failed download, a hard schema-drift/vocabulary error in
the standardizer (see praha/scripts/standardize.py docstring for what counts as "expected,
documented" vs. "unexplained, fail loudly"), or a failed G1 schema validation. The documented
substitute-vote pattern (R1) does NOT fail the run — it is logged and included in the quality
report instead (see the plan's G2 acceptance check).

IMPORTANT operational note (added C8, 2026-08-04): `standardize.standardize()` writes
organizations.csv/memberships.csv FROM SCRATCH each run — it has no knowledge of the
`praha:org:candidate-list:*` rows `praha/scripts/party_affiliation.py` adds (candidate-list/party
affiliation, D7's fallback; see that script's module docstring). Running this pipeline WITHOUT a
subsequent `party_affiliation.py` run will silently drop those rows back to just the bare assembly
membership. This orchestrator does NOT currently call party_affiliation.py automatically (it hits
a different, non-Golemio source — volby.cz — on a different cadence: candidate-list affiliation is
fixed at election time and essentially never changes, unlike the nightly-updated vote CSV, so
re-running it every night would be wasted network calls for data that cannot change). Whoever wires
up C6 (nightly workflow) must either (a) run `party_affiliation.py` after every `run_pipeline.py`
invocation regardless, accepting the wasted calls, or (b) run it once and thereafter treat
`organizations.csv`/`memberships.csv`'s candidate-list rows as a merge target this orchestrator
must preserve across re-runs (e.g. read-modify-write instead of overwrite). Not decided here —
flagging for C6, not fixing it as part of C8.
"""
import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

_CITY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _CITY_ROOT.parent

sys.path.insert(0, str(_CITY_ROOT / "scripts"))
import downloader  # noqa: E402
import standardize  # noqa: E402


def _run_validator(script_name: str, data_dir: Path) -> None:
    script_path = _REPO_ROOT / "scripts" / script_name
    cmd = [sys.executable, str(script_path), "--data-dir", str(data_dir)]
    logging.info("Running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if result.stdout:
        logging.info("%s stdout:\n%s", script_name, result.stdout.strip())
    if result.returncode != 0:
        logging.error("%s stderr:\n%s", script_name, result.stderr.strip())
        raise RuntimeError(f"{script_name} failed (G1 schema gate) — see log above")
    logging.info("%s passed (G1)", script_name)


def run(skip_download: bool = False) -> dict:
    raw_path = Path(downloader._DEFAULT_OUT)
    if not skip_download:
        raw_path = downloader.download()
    elif not raw_path.exists():
        raise FileNotFoundError(f"--skip-download given but {raw_path} does not exist")
    else:
        logging.info("Skipping download, reusing %s", raw_path)

    data_dir = _CITY_ROOT / "data"
    report = standardize.standardize(raw_csv_path=raw_path, out_dir=data_dir)

    # G1: schema gate (shared validators)
    _run_validator("validate_tables.py", data_dir)
    _run_validator("validate_records.py", data_dir)

    # G2: source cross-check summary (already computed inside standardize(); the standardizer
    # itself hard-fails on anything outside the documented pattern, so reaching this point means
    # G2 passed, with the known substitute-vote gap explicitly logged, not hidden).
    logging.info(
        "G2 cross-check summary: %d/%d rows exact match, %d rows documented substitute-vote gap "
        "(logged), %d rows had no published aggregate (logged)",
        report["exact_match"],
        report["total_rows"],
        len(report["unresolved_substitute_gap"]),
        len(report["aggregate_not_published"]),
    )

    report_path = _CITY_ROOT / "work" / "reports" / "g2_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logging.info("Wrote full quality report to %s (gitignored, work/)", report_path)

    logging.info("Praha pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="reuse the previously downloaded raw CSV instead of fetching again",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download)


if __name__ == "__main__":
    main()
