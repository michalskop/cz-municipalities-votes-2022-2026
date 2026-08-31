"""Pardubice pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check (G2).

Usage:
    python pardubice/pipelines/run_pipeline.py
    python pardubice/pipelines/run_pipeline.py --skip-download   # reuse the cached raw/ files

Exits non-zero on: a failed download, a hard error in the standardizer (an unhandled format
variant), a failed G1 schema validation, or a G2 breach.

G2 here has two parts, both built into standardize.py's resolve_all_events():
  1. Per-event count consistency — each event's recomputed per-row Pro/Proti/abstain/not-voting
     must match the source's own totals line. Gated at a small bounded rate (not 0 like Ústí):
     one real event, meeting 10 vote 21, has a source-side totals-line error (it omits
     "Nehlasovalo: 1" although its own roster shows one councillor as Nehlasoval, and 36 Pro + 1
     Nehlasoval + 2 Omluven = 39 = the stated Celkem). Our person-level data is the more correct
     of the two there; the roster-vs-Celkem check (part 2) still passes for that event.
  2. Roster completeness — each event's roster-row count must equal the source's own "Celkem
     zastupitelů" figure. Gated at 0 tolerance (a first full run had 1546/1546).

No party-affiliation/coalition step here (matches every other city's C9 precedent) — building real
klub organizations from the per-vote klub text is C4's job (needs D7 owner sign-off).

Requires `pdftotext` (poppler-utils) on PATH.
"""
import argparse
import logging
import subprocess
import sys
from pathlib import Path

_CITY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _CITY_ROOT.parent

sys.path.insert(0, str(_CITY_ROOT / "scripts"))
import downloader  # noqa: E402
import standardize  # noqa: E402

_G2_COUNT_MAX_MISMATCH_RATE = 0.005  # ~<=7 of 1546; a first full run had exactly 1 (see docstring)


def _run_validator(script_name: str, data_dir: Path) -> None:
    script_path = _REPO_ROOT / "scripts" / script_name
    cmd = [sys.executable, str(script_path), "--data-dir", str(data_dir)]
    logging.info("Running %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=_REPO_ROOT, capture_output=True, text=True)
    if result.stdout:
        logging.info("%s stdout:\n%s", script_name, result.stdout.strip())
    if result.returncode != 0:
        logging.error("%s stderr:\n%s", script_name, result.stderr.strip())
        raise RuntimeError(f"{script_name} failed (G1 schema gate) -- see log above")
    logging.info("%s passed (G1)", script_name)


def run(skip_download: bool = False) -> dict:
    raw_dir = downloader._DEFAULT_OUT_DIR
    if not skip_download:
        downloader.download()
    elif not (raw_dir / "manifest.json").exists():
        raise FileNotFoundError(f"--skip-download given but {raw_dir / 'manifest.json'} does not exist")
    else:
        logging.info("Skipping download, reusing cached corpus under %s", raw_dir)

    data_dir = _CITY_ROOT / "data"
    report = standardize.standardize(raw_dir=raw_dir, out_dir=data_dir)

    _run_validator("validate_tables.py", data_dir)
    _run_validator("validate_records.py", data_dir)

    cc = report["count_consistency"]
    total = max(1, report["total_events"])
    rate = cc["mismatch"] / total
    logging.info("G2 count-consistency: %d match / %d mismatch of %d events (%.3f%%)",
                 cc["match"], cc["mismatch"], report["total_events"], rate * 100)
    for mm in report["count_mismatches"]:
        logging.warning("G2 count mismatch: %s", mm)
    if rate > _G2_COUNT_MAX_MISMATCH_RATE:
        raise RuntimeError(
            f"G2 gate: per-event count-mismatch rate {rate:.2%} exceeds the {_G2_COUNT_MAX_MISMATCH_RATE:.1%} "
            f"bound -- this is a parsing regression, not the handful of known source-side totals-line "
            f"errors. First mismatch: {report['count_mismatches'][0] if report['count_mismatches'] else None}"
        )

    rv = report["roster_vs_celkem"]
    logging.info("G2 roster completeness: %d match / %d mismatch", rv["match"], rv["mismatch"])
    if rv["mismatch"] > 0:
        raise RuntimeError(
            f"G2 gate: {rv['mismatch']} event(s) have a roster-row count != the source's own "
            f"'Celkem zastupitelů' -- a row was dropped or double-counted. Refusing to pass silently. "
            f"First: {report['roster_mismatches'][0] if report['roster_mismatches'] else None}"
        )

    unmapped = len(report["unmapped_options"])
    total_votes = report["votes_count"]
    unmapped_rate = unmapped / max(1, total_votes + unmapped)
    logging.info("G2: %d/%d votes had an unmapped option (%.2f%%)", unmapped, total_votes + unmapped, unmapped_rate * 100)
    if unmapped_rate > 0.02:
        raise RuntimeError(
            f"G2 gate: unmapped vote-option rate {unmapped_rate:.2%} exceeds the 2% bound -- "
            "a real new vocabulary value or a parsing bug. Refusing to pass silently."
        )

    if report["missing_result"]:
        logging.warning("%d event(s) had no SCHVÁLENO/NESCHVÁLENO result line (defaulted to 'pass'): %s",
                        len(report["missing_result"]), report["missing_result"][:10])

    logging.info("Pardubice pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-download", action="store_true", help="reuse the cached raw/ corpus instead of re-fetching")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download)


if __name__ == "__main__":
    main()
