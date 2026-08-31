"""České Budějovice pipeline orchestrator: download -> standardize -> validate (G1) -> cross-check
(G2).

Usage:
    python ceske-budejovice/pipelines/run_pipeline.py
    python ceske-budejovice/pipelines/run_pipeline.py --skip-download   # reuse cached raw/ HTML

Exits non-zero on: a failed term-index/meeting fetch, a hard error in the standardizer, a failed
G1 schema validation, or a G2 breach.

G2 for České Budějovice — the portal's per-vote pages publish NO aggregate tally and NO
přijato/nepřijato outcome, so there is no independent figure to self-check against (the same
"self-consistency is acceptable when no independent aggregate exists" stance as Ostrava). G2 here
is therefore:
  1. Roster completeness — each event's parsed councillor-row count must equal the 45-member
     council. Gated at a small bounded rate (a genuinely under-full meeting early/late in a term,
     or a mid-term vacancy before a náhradník is seated, can legitimately differ by 1-2).
  2. Vote-vocabulary coverage — the unmapped-cast-value rate must stay under 2%.
  3. Heading/meeting-number consistency — each vote page's own "zasedání číslo NNN" heading must
     match the meeting it was crawled under (a wrong ?bod= id would surface here).

`result` is DERIVED (pass iff >= 23 "Hlasoval pro", § 87 zákona č. 128/2000 Sb.) and every
vote_event's sources note says so — it is not presented as coming from the portal.
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

_ROSTER_MAX_MISMATCH_RATE = 0.05
_UNMAPPED_MAX_RATE = 0.02


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

    rv = report["roster_vs_council"]
    total = max(1, report["total_events"])
    rate = rv["mismatch"] / total
    logging.info("G2 roster completeness: %d match / %d mismatch of %d events (%.2f%%)",
                 rv["match"], rv["mismatch"], report["total_events"], rate * 100)
    for mm in report["roster_mismatches"][:20]:
        logging.warning("G2 roster mismatch: %s", mm)
    if rate > _ROSTER_MAX_MISMATCH_RATE:
        raise RuntimeError(
            f"G2 gate: {rate:.1%} of events have a councillor-row count != {standardize.COUNCIL_SIZE} "
            f"(bound {_ROSTER_MAX_MISMATCH_RATE:.0%}) -- likely a table-parsing regression, not "
            f"legitimate vacancies. First: {report['roster_mismatches'][0] if report['roster_mismatches'] else None}"
        )

    unmapped = len(report["unmapped_options"])
    total_votes = report["votes_count"]
    urate = unmapped / max(1, total_votes + unmapped)
    logging.info("G2: %d/%d cast values unmapped (%.2f%%)", unmapped, total_votes + unmapped, urate * 100)
    if urate > _UNMAPPED_MAX_RATE:
        raise RuntimeError(
            f"G2 gate: unmapped cast-value rate {urate:.2%} exceeds the {_UNMAPPED_MAX_RATE:.0%} bound "
            f"-- a real new vocabulary value or a parsing bug. First: {report['unmapped_options'][0] if report['unmapped_options'] else None}"
        )

    hm = report["heading_meeting_mismatch"]
    if hm:
        logging.error("G2 gate: %d vote page(s) whose own 'zasedání číslo' heading disagrees with the "
                      "meeting they were crawled under: %s", len(hm), hm[:10])
        raise RuntimeError("G2 gate: vote-page heading/meeting-number mismatch -- a wrong ?bod= id was crawled.")

    if report["skipped_failed_fetch"]:
        logging.warning("%d vote page(s) were skipped (failed fetch, recorded in manifest failed_votes)",
                        report["skipped_failed_fetch"])

    logging.info("České Budějovice pipeline finished: G1 and G2 gates passed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-download", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(skip_download=args.skip_download)


if __name__ == "__main__":
    main()
