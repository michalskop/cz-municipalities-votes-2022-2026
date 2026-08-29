"""Ústí nad Labem pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check
(G2).

Usage:
    python usti-nad-labem/pipelines/run_pipeline.py
    python usti-nad-labem/pipelines/run_pipeline.py --skip-download   # reuse the cached raw/ PDFs

Exits non-zero if any stage fails: a failed download, a hard error in the standardizer (a real
format variant not yet tolerated -- see standardize.py's module docstring for the several already
found and handled), or a failed G1 schema validation. There is no G2 self-consistency mismatch
concept here the way other cities have it (Praha/Brno/etc. compare a recomputed majority against
a separately-stated source result) -- instead, G2 here is the PER-EVENT count-consistency check
already built into the corpus itself: every event's Pro/Proti/Zdržel se/Nehlasoval counts and
present-count ("Hlasoval") are cross-checked against the source's own totals line during
standardize.py's resolve_all_events() (confirmed 0/562 mismatches on the real corpus during the
build) -- a parsing-bug mismatch here would show up as a hard error (an assertion this file adds),
not a silent tolerance, since ANY mismatch would mean the regex-based column reconstruction is
unreliable, not an expected supermajority/quorum exception like other cities' G2 checks model.

No party-affiliation/coalition step here (matches every other city's C2 precedent): building real
party/klub organizations from the klub text already captured per vote is C4's job, needing owner
sign-off (D7), not C9's.

Requires `pdftotext` (poppler-utils) on PATH, same as Plzeň.
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

    # G2, hard gate (0 tolerance, unlike other cities' bounded supermajority/quorum exceptions --
    # see module docstring): every event's recomputed per-option counts must match the source's
    # own totals line exactly. A real first-run confirmed 0/562 mismatches across the full corpus.
    cc = report["count_consistency"]
    logging.info("G2 count-consistency: %d match, %d mismatch out of %d events", cc["match"], cc["mismatch"], report["total_events"])
    if cc["mismatch"] > 0:
        raise RuntimeError(
            f"G2 gate: {cc['mismatch']} event(s) have a count mismatch against the source's own "
            f"totals line -- this means the regex-based column reconstruction is unreliable for "
            f"at least one real event, not an expected exception. Refusing to pass silently. "
            f"First mismatch: {report['count_mismatches'][0] if report['count_mismatches'] else None}"
        )

    unmapped = len(report["unmapped_options"])
    total_votes = report["votes_count"]
    unmapped_rate = unmapped / max(1, total_votes + unmapped)
    logging.info("G2: %d/%d votes had an unmapped option (%.2f%%)", unmapped, total_votes + unmapped, unmapped_rate * 100)
    if unmapped_rate > 0.02:
        raise RuntimeError(
            f"G2 gate: unmapped vote-option rate {unmapped_rate:.2%} exceeds the 2% bound -- "
            "this looks like a real new vocabulary value or a parsing bug. Refusing to pass silently."
        )

    logging.info("Ústí nad Labem pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-download", action="store_true", help="reuse the cached raw/ corpus instead of re-fetching")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download)


if __name__ == "__main__":
    main()
