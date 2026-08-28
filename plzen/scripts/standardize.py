"""Standardize Plzeň's ZMP roll-call votes (usneseni.plzen.eu) into dt-standard tables.

Source shape fully documented in plzen/config/sources.yml (research pass 2026-08-28 -- see this
repo's memory: plzen-source-research). Summary: `plzen/scripts/downloader.py` fetches, per agenda
point, (1) the point's own HTML page (title/proposer/press-number -- reliable in every era) and
(2) a "Protokol o hlasování" vote-protocol file in ONE OF THREE DIFFERENT FORMATS depending on
when the meeting was held:

  - era1 (2022-10-18 .. 2024-06-20): cp1250-encoded HTML, no BOM. Per-person votes as inline
    "Name: Vote" pairs grouped by klub <th> headers. No title/given/family split marker -- names
    are split against the era2-built canonical roster (see _resolve_person below), with a
    first-word/rest heuristic fallback only for people never seen in era2.
  - era2 (2024-09-19 .. 2026-03-26): UTF-16LE-encoded HTML with BOM. A real <table>, one
    <tr class="value"> per person, family name reliably wrapped in <b>...</b> -- the AUTHORITATIVE
    source for each person's given_name/family_name split, used to build the canonical roster.
  - era3 (2026-05-14 .., CURRENT/ONGOING): real text-layer PDF, extracted via `pdftotext -layout`
    (poppler-utils, a required system dependency). The PDF's embedded font subset has NO
    ToUnicode CMap, so poppler's Unicode guess is wrong for an unpredictable, PER-FILE-RANDOM
    subset of characters (confirmed: the exact corruption pattern differs between different PDFs,
    it is not one fixed cipher) -- see _build_era3_char_map's docstring for how this is handled:
    a per-file character-substitution map is bootstrapped by comparing each row's (possibly
    garbled) name text, by ROW POSITION, against the already-known canonical roster (position
    order is confirmed stable across all three eras -- alphabetical by family name, unchanged
    from era2 into era3 in every sample checked), then applied to that same file's klub/vote-value
    columns. The person's IDENTITY itself always comes from roster POSITION, never from trusting
    a single file's own (possibly-corrupted) name text -- this is deliberate and load-bearing, not
    just a fallback.

Vote option vocabulary (5-way, both spellings map to the same dt-standard options):
    era1: pro/proti/"zdržel se"/nehlasoval/omluven (lowercase-ish)
    era2/3: PRO/PROTI/"ZDRŽEL SE"/NEHLASOVAL/NEPŘÍTOMEN (uppercase)
    "omluven" (era1, excused) and "NEPŘÍTOMEN" (era2/3, absent) both map to dt-standard's
    "absent" -- both mean "not present", era1 just has a slightly different label for it.
    era3's NEPŘÍTOMEN column value is determined primarily via the reliable Karta-empty signal
    (an empty "Karta"/card-number cell means the person's voting card was never checked in this
    session -- confirmed to correlate perfectly with NEPŘÍTOMEN in every sample checked), not by
    trusting the (possibly garbled) vote-value text directly; the derived per-file character map
    is used as a cross-check, and any disagreement is logged, never silently guessed past.

Scope boundary (matches every other city's precedent): builds ONLY the bare assembly organization
+ membership. No party/coalition organizations here -- klub text IS captured per vote (needed
later for C4), but building real klub organizations/memberships from it is a separate script.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import pandas as pd

_CITY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_RAW_DIR = _CITY_ROOT / "work" / "raw"
_DEFAULT_OUT = _CITY_ROOT / "data"

ORG_ID = "plzen:org:zastupitelstvo-mesta-plzne"
ORG_NAME = "Zastupitelstvo města Plzně"

_OPTION_MAP = {
    # era1: title-case ("Pro", not "pro" -- confirmed against the real corpus, not just the
    # initial sample). Also has TWO distinct "not present" labels -- "omluven" (excused, lowercase)
    # and "nepřít." (abbreviated nepřítomen/absent, lowercase). Both map to dt-standard's "absent";
    # kept as two source labels rather than merged upstream so the raw distinction stays visible.
    "Pro": "yes", "Proti": "no", "Zdržel se": "abstain", "Nehlasoval": "not voting",
    "omluven": "absent", "nepřít.": "absent",
    "PRO": "yes", "PROTI": "no", "ZDRŽEL SE": "abstain", "NEHLASOVAL": "not voting", "NEPŘÍTOMEN": "absent",
}
_TITLE_PREFIXES = {
    "ing", "mgr", "bc", "judr", "mudr", "phdr", "et", "ing.", "mgr.", "bc.", "judr.", "mudr.", "phdr.", "et.",
}


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _detect_format(raw: bytes) -> str:
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "era2"
    if raw[:4] == b"%PDF":
        return "era3"
    return "era1"


# ── Point page (title/proposer/press-number -- reliable in every era) ──────────────────────────
def parse_point_page(html: str) -> dict[str, Any]:
    m1 = re.search(r"<h1>Bod č\. (\d+)\.\s*-\s*([^<:]+):</h1>", html)
    m2 = re.search(r"<h2>(.*?)</h2>", html, re.S)
    m3 = re.search(r"\(předkladatel:\s*(.*?)\)", html, re.S)
    title = re.sub(r"\s+", " ", m2.group(1)).strip() if m2 else None
    return {
        "bod_number": m1.group(1) if m1 else None,
        "press_number": m1.group(2).strip() if m1 else None,
        "title": title,
        "proposer": re.sub(r"\s+", " ", m3.group(1)).strip() if m3 else None,
    }


# ── Era 1: cp1250 HTML, inline "Name: Vote" pairs grouped by klub ──────────────────────────────
_ERA1_HEADER_RE = re.compile(
    r"(\d+)\. zasedání Zastupitelstva města Plzně ze dne\s+(\d+)\.(\d+)\.(\d+) - (\d+):(\d+):(\d+)"
)
_ERA1_VOTE_NO_RE = re.compile(r"(\d+)\. hlasování")
_ERA1_RESULT_RE = re.compile(r"<b>(Návrh (?:ne)?byl přijat)</b>")
_ERA1_TOTALS_RE = re.compile(
    r"Přítomno: (\d+)</td><td[^>]*>Pro: (\d+)</td><td[^>]*>Proti: (\d+)</td><td[^>]*>Zdržel se: (\d+)</td>"
)
_ERA1_KLUB_BLOCK_RE = re.compile(
    r'<th nowrap>([^<]+)</th><th[^>]*>\(Pro: \d+, Proti: \d+, Zdržel se: \d+\)</th>\s*'
    r'</tr></table></td></tr><tr><td><table[^>]*>(.*?)</table></td></tr></table>',
    re.S,
)
_ERA1_PAIR_RE = re.compile(r'class="votename">([^<]*)</td>\s*<td nowrap class="votechoice">([^<]*)</td>')


def parse_era1(raw: bytes) -> dict[str, Any]:
    text = raw.decode("cp1250")

    header = _ERA1_HEADER_RE.search(text)
    date = f"{header.group(4)}-{header.group(3).zfill(2)}-{header.group(2).zfill(2)}" if header else None
    time = f"{header.group(5)}:{header.group(6)}:{header.group(7)}" if header else None

    vote_no_m = _ERA1_VOTE_NO_RE.search(text)
    result_m = _ERA1_RESULT_RE.search(text)
    totals_m = _ERA1_TOTALS_RE.search(text)

    votes: list[dict[str, Any]] = []
    for klub, block in _ERA1_KLUB_BLOCK_RE.findall(text):
        for name, vote in _ERA1_PAIR_RE.findall(block):
            name = name.strip().rstrip(":").strip()
            vote = vote.strip()
            if not name or name == "&nbsp;" or not vote or vote == "&nbsp;":
                continue
            votes.append({"name_raw": name, "klub": klub.strip(), "option_raw": vote})

    return {
        "era": "era1",
        "date": date,
        "time": time,
        "vote_no": vote_no_m.group(1) if vote_no_m else None,
        "result_text": result_m.group(1) if result_m else None,
        "totals": {
            "pritomno": int(totals_m.group(1)), "pro": int(totals_m.group(2)),
            "proti": int(totals_m.group(3)), "zdrzel": int(totals_m.group(4)),
        } if totals_m else {},
        "votes": votes,
    }


# ── Era 2: UTF-16LE HTML, real <table>, family name reliably <b>-wrapped ───────────────────────
_ERA2_HEADER_RE = re.compile(
    r"Zasedání č\. (\d+) - (\d+)\. ZMP [\d. ]+</td>\s*"
    r'<td class="label_r">Dne (\d+)\.(\d+)\.(\d+) (\d+):(\d+):(\d+)</td>'
)
_ERA2_TOTALS_RE = re.compile(
    r'<tr class="totals">\s*<td>PŘÍTOMNÝCH:</td>\s*<td>(\d+)</td>\s*<td><b>PRO:</b></td>\s*<td><b>(\d+)</b></td>\s*'
    r'<td>ZDRŽELO SE:</td>\s*<td>(\d+)</td>\s*</tr>\s*<tr class="totals">\s*<td>NEPŘÍTOMNÝCH:</td>\s*<td>(\d+)</td>\s*'
    r'<td>PROTI:</td>\s*<td>(\d+)</td>\s*<td>NEHLASOVALO:</td>\s*<td>(\d+)</td>'
)
_ERA2_ROW_RE = re.compile(r'<tr class="value">(.*?)</tr>', re.S)
_ERA2_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)


def parse_era2(raw: bytes) -> dict[str, Any]:
    text = raw.decode("utf-16")

    header = _ERA2_HEADER_RE.search(text)
    date = f"{header.group(5)}-{header.group(4).zfill(2)}-{header.group(3).zfill(2)}" if header else None
    time = f"{header.group(6)}:{header.group(7)}:{header.group(8)}" if header else None

    totals_m = _ERA2_TOTALS_RE.search(text)

    votes: list[dict[str, Any]] = []
    for row in _ERA2_ROW_RE.findall(text):
        tds = _ERA2_TD_RE.findall(row)
        if len(tds) < 6:
            continue
        radek, _karta, _blank, name_html, klub, vote = tds[:6]
        m = re.match(r"(.*?)<b>(.*?)</b>", name_html, re.S)
        if m:
            title_given = re.sub("<[^>]+>", "", m.group(1)).strip()
            family = re.sub("<[^>]+>", "", m.group(2)).strip()
        else:
            title_given, family = "", re.sub("<[^>]+>", "", name_html).strip()
        # Some meetings append a post-nominal degree suffix INSIDE the bold family-name span
        # (e.g. "<b>Šlouf, MBA</b>" once a councilor added an MBA mid-term) while others show the
        # same person's plain "<b>Šlouf</b>" -- confirmed via the real corpus (5 people affected)
        # to be the SAME real person, not a new one. Stripping anything from the first comma
        # onward keeps person identity stable across meetings; the degree itself isn't modeled.
        family = family.split(",")[0].strip()
        votes.append(
            {
                "radek": int(radek) if radek.strip().isdigit() else None,
                "title_given": re.sub(r"\s+", " ", title_given).strip(),
                "family": re.sub(r"\s+", " ", family).strip(),
                "klub": re.sub(r"\s+", " ", klub).strip(),
                "option_raw": vote.strip(),
            }
        )

    return {
        "era": "era2",
        "date": date,
        "time": time,
        "meeting_no": header.group(2) if header else None,
        "totals": {
            "pritomno": int(totals_m.group(1)), "pro": int(totals_m.group(2)), "zdrzel": int(totals_m.group(3)),
            "nepritomno": int(totals_m.group(4)), "proti": int(totals_m.group(5)), "nehlasovalo": int(totals_m.group(6)),
        } if totals_m else {},
        "votes": votes,
    }


# ── Era 3: PDF, pdftotext -layout + positional roster matching ─────────────────────────────────
_ERA3_DNE_RE = re.compile(r"Dne:?\s*(\d+)\.(\d+)\.(\d+)\s+(\d+):(\d+)")
_ERA3_ROW_RE = re.compile(r"^\s*(\d+)\s+(.*)$")
_ERA3_CLEAN_OPTIONS = {"PRO", "PROTI", "ZDRŽEL SE", "NEHLASOVAL"}


def _pdftotext_layout(raw: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(raw)
        tmp.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", tmp.name, "-"], capture_output=True, check=True
        )
    return result.stdout.decode("utf-8")


def _extend_char_map(mapping: dict[str, str], cell: str, canonical: str) -> int:
    """Learns character substitutions from one (possibly-garbled cell, known-correct text) pair,
    updating `mapping` in place. Only usable when the two strings have equal length (garbling
    substitutes characters 1:1, never adds/removes any -- confirmed throughout testing). Returns
    the number of NEW conflicting mappings encountered (logged by the caller, not raised -- see
    module docstring: this is a best-effort bootstrap, never a hard requirement)."""
    cell_norm = re.sub(r"\s+", " ", cell.strip())
    canon_norm = re.sub(r"\s+", " ", canonical.strip())
    if len(cell_norm) != len(canon_norm):
        return 0
    conflicts = 0
    for g, c in zip(cell_norm, canon_norm):
        if g == c:
            continue
        if g in mapping and mapping[g] != c:
            conflicts += 1
            continue
        mapping[g] = c
    return conflicts


def _build_era3_char_map(
    name_cells: list[str], canonical_names: list[str], klub_cells: list[str], known_klubs: set[str]
) -> dict[str, str]:
    """Bootstraps a PER-FILE character-substitution map (see module docstring's era3 section: the
    font-encoding corruption's exact cipher differs between PDFs, so a single hardcoded table
    doesn't generalize -- this derives that file's own map from data already known to be correct).
    Two training sources, in order:
    1. Name cells vs. the already-known roster, matched by ROW POSITION (see parse_era3) -- the
       primary source, covers most Latin letters and common diacritics.
    2. Klub cells vs. the known_klubs vocabulary (from era1/era2), matched by LENGTH -- fills in
       characters that never happen to appear in any of the 47 roster names (e.g. 'Č' in "Česká
       pirátská strana" was missing from the name-only map in real testing) but DO appear in a
       klub name. Only applied when exactly one known klub has the matching length (ambiguous
       length matches are skipped -- never guess between two same-length candidates)."""
    mapping: dict[str, str] = {}
    conflicts = 0
    for cell, canonical in zip(name_cells, canonical_names):
        conflicts += _extend_char_map(mapping, cell, canonical)

    by_length: dict[int, list[str]] = {}
    for k in known_klubs:
        by_length.setdefault(len(k), []).append(k)
    for cell in klub_cells:
        cell_norm = re.sub(r"\s+", " ", cell.strip())
        if cell_norm in known_klubs:
            continue  # already clean, nothing to learn
        candidates = by_length.get(len(cell_norm), [])
        # Case-insensitive duplicates (e.g. known_klubs holding both "PRO PLZEŇ" and "Pro Plzeň"
        # for the same real klub, seen with different casing across meetings) aren't a genuine
        # ambiguity -- any one of them teaches the same underlying letter shapes.
        distinct_casefold = {c.casefold() for c in candidates}
        if len(distinct_casefold) == 1 and candidates:
            # Deterministic pick among case-variant duplicates (their iteration order isn't
            # stable run-to-run, which previously caused spurious same-file conflicts): prefer
            # the all-uppercase spelling, matching era2/3's general labeling convention.
            best = max(candidates, key=lambda c: (c == c.upper(), c))
            conflicts += _extend_char_map(mapping, cell_norm, best)

    if conflicts:
        logging.warning("Era3 char-map: %d conflicting character mappings ignored (first-seen kept)", conflicts)
    return mapping


def parse_era3(
    raw: bytes, roster: list[dict[str, str]], known_klubs: set[str] = frozenset()
) -> dict[str, Any]:
    """roster: ordered list (same order as the protocol's own Řádek numbering -- alphabetical by
    family name, confirmed stable across all three eras) of {"title_given", "family", "person_key"}.
    known_klubs: klub names already seen in era1/era2 -- used to resolve each row's klub cell
    WITHOUT blindly applying the per-file character map (era3's font corruption is per-cell, not
    uniform across a file -- some cells are already clean; translating an already-clean cell would
    corrupt it, since the map's keys can coincide with valid characters. Strategy: try the raw
    text first (already clean, most common case per testing), then the char-map-translated text,
    only falling back to the raw (possibly garbled) text with a logged warning if neither matches
    a known klub -- never silently trust a translated guess that doesn't match anything real).
    KNOWN LIMITATION (accepted, not blocking -- klub/party data is out of C2's scope, see module
    docstring's Scope boundary): the broken font appears to reuse the SAME glyph for a letter's
    upper- and lower-case forms in some positions, so a map trained mostly on mixed-case NAME text
    can produce a wrong-case (but otherwise legible) guess for an all-caps klub label it has no
    other calibration data for -- observed on a handful of fully-corrupted "PRO PLZEŇ" cells in
    real testing. These fail the known-klub match (case-sensitive) and correctly fall through to
    the flagged raw-text case rather than silently accepting the wrong-case guess."""
    text = _pdftotext_layout(raw)
    lines = text.splitlines()

    dne = _ERA3_DNE_RE.search(text)
    date = f"{dne.group(3)}-{dne.group(2).zfill(2)}-{dne.group(1).zfill(2)}" if dne else None
    time = f"{dne.group(4)}:{dne.group(5)}:00" if dne else None

    header_idx = next((i for i, l in enumerate(lines) if "Karta" in l or "Zastupitel" in l), None)
    if header_idx is None:
        raise ValueError("era3 PDF: table header line not found -- layout changed?")
    header_line = lines[header_idx]
    col_karta = header_line.find("Karta")
    col_zastupitel = header_line.find("Zastupitel")
    col_strana = header_line.find("Strana")
    col_hlasoval = header_line.find("Hlasoval")
    if -1 in (col_karta, col_zastupitel, col_strana, col_hlasoval):
        raise ValueError(f"era3 PDF: could not locate expected columns in header line: {header_line!r}")

    data_lines = []
    for l in lines[header_idx + 1:]:
        m = _ERA3_ROW_RE.match(l)
        if m:
            data_lines.append(l)
        elif l.strip() == "" and data_lines:
            break  # blank line after the table body ends the row block

    name_cells = [l[col_zastupitel:col_strana] for l in data_lines]
    klub_cells = [l[col_strana:col_hlasoval] for l in data_lines]
    canonical_names = [
        f"{r['title_given']} {r['family']}".strip() if i < len(roster) else "" for i, r in enumerate(roster)
    ][: len(name_cells)]
    char_map = _build_era3_char_map(name_cells, canonical_names, klub_cells, known_klubs)
    tbl = str.maketrans(char_map)

    votes: list[dict[str, Any]] = []
    for i, l in enumerate(data_lines):
        karta_cell = l[col_karta:col_zastupitel]
        karta_empty = not re.search(r"\d", karta_cell)
        klub_cell = l[col_strana:col_hlasoval]
        vote_cell = l[col_hlasoval:]

        klub_raw = re.sub(r"\s+", " ", klub_cell).strip()
        klub_translated = re.sub(r"\s+", " ", klub_cell.translate(tbl)).strip()
        known_by_casefold = {k.casefold(): k for k in known_klubs}
        if klub_raw in known_klubs:
            klub_fixed = klub_raw
        elif klub_translated in known_klubs:
            klub_fixed = klub_translated
        elif klub_translated.casefold() in known_by_casefold:
            # The translated text is a real known klub, just wrong-cased -- see
            # _build_era3_char_map's KNOWN LIMITATION docstring (the font can reuse one glyph for
            # both cases of a letter, so a name-trained map sometimes gets an all-caps label's
            # case wrong even though every LETTER is otherwise correct). Casefold-matching
            # recovers these instead of leaving them as a phantom "unknown klub".
            klub_fixed = known_by_casefold[klub_translated.casefold()]
        else:
            klub_fixed = klub_raw
            logging.warning(
                "era3 row %d: klub cell %r (translated: %r) matches no known klub -- keeping raw "
                "text, flagged for review", i, klub_raw, klub_translated,
            )
        vote_clean = vote_cell.strip()

        if vote_clean in _ERA3_CLEAN_OPTIONS:
            option_raw = vote_clean
        elif karta_empty:
            option_raw = "NEPŘÍTOMEN"
            if vote_clean not in _ERA3_CLEAN_OPTIONS and vote_clean != "":
                # cross-check: does the char-map-fixed text agree?
                fixed_vote = re.sub(r"\s+", " ", vote_cell.translate(tbl)).strip()
                if fixed_vote != "NEPŘÍTOMEN":
                    logging.warning(
                        "era3 row %d: Karta empty (inferred NEPŘÍTOMEN) but char-map-fixed vote "
                        "text reads %r -- flagging, using the Karta-based inference",
                        i, fixed_vote,
                    )
        else:
            logging.warning("era3 row %d: unrecognized vote text %r (Karta not empty) -- skipping, not fabricated", i, vote_clean)
            continue

        votes.append(
            {
                "roster_index": i,
                "klub": klub_fixed,
                "option_raw": option_raw,
            }
        )

    return {"era": "era3", "date": date, "time": time, "totals": {}, "votes": votes}


# ── Person resolution (shared across eras) ──────────────────────────────────────────────────────
def _split_era1_name(name_raw: str, known_family_names: set[str]) -> tuple[str, str]:
    """First-word-is-given-name heuristic, EXCEPT when the remainder matches an already-known
    (from era2) multi-word family name exactly -- avoids mis-splitting compound surnames like
    'Jilichová Nová' for people also seen in era2. For era1-only people with a genuinely
    unknown compound surname, this can still mis-split; not observed as an issue in this
    roster (checked against the full corpus during standardize()'s own logging)."""
    parts = name_raw.split(" ", 1)
    if len(parts) == 1:
        return parts[0], ""
    given, rest = parts
    if rest in known_family_names:
        return given, rest
    # try progressively shorter "rest" against known multi-word surnames (rare compound case)
    rest_words = rest.split(" ")
    for n in range(len(rest_words), 0, -1):
        candidate_family = " ".join(rest_words[-n:])
        if candidate_family in known_family_names:
            candidate_given = " ".join([given] + rest_words[: len(rest_words) - n])
            return candidate_given, candidate_family
    return given, rest


def _strip_titles(title_given: str) -> str:
    words = title_given.split()
    while words and _slugify(words[0]).rstrip("-") in _TITLE_PREFIXES:
        words.pop(0)
    return " ".join(words) if words else title_given


# ── Orchestration ────────────────────────────────────────────────────────────────────────────
def _load_manifest(raw_dir: Path) -> dict[str, Any]:
    return json.loads((raw_dir / "manifest.json").read_text(encoding="utf-8"))


def _iter_protocols(raw_dir: Path, manifest: dict[str, Any]):
    """Yields (meeting, point_id, raw_bytes, era) for every point with a cached protocol, in
    manifest (chronological) order. Missing files are skipped with a warning, never raise --
    matches every other city's "log + skip, don't abort the run" incremental-scrape tolerance."""
    for meeting in manifest["meetings"]:
        for point_id in meeting["points_with_protocol"]:
            path = raw_dir / "protocols" / f"{point_id}.html"
            if not path.exists():
                logging.warning("Meeting %d point %d: protocol listed in manifest but file missing on disk", meeting["meeting_id"], point_id)
                continue
            raw = path.read_bytes()
            yield meeting, point_id, raw, _detect_format(raw)


def _point_page_meta(raw_dir: Path, point_id: int) -> dict[str, Any]:
    path = raw_dir / "points" / f"{point_id}.html"
    if not path.exists():
        return {"bod_number": None, "press_number": None, "title": None, "proposer": None}
    return parse_point_page(path.read_text(encoding="utf-8"))


def _person_key(given: str, family: str) -> str:
    return _slugify(f"{given}-{family}")


def resolve_all_events(raw_dir: Path = _DEFAULT_RAW_DIR) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]], dict[str, Any]]:
    """Core parsing/resolution logic, shared by standardize() (builds the dt-standard tables) and
    party_affiliation.py (derives klub-history intervals from the SAME per-event klub text this
    already resolves -- see this file's module docstring's Scope boundary: klub text is captured
    here but building real organizations/memberships from it is a separate, later script).
    Returns (events, persons, report). Each event's "options" list carries "klub" per person, not
    just "option" -- this is the only place that field is populated."""
    manifest = _load_manifest(raw_dir)
    protocols = list(_iter_protocols(raw_dir, manifest))
    report: dict[str, Any] = {
        "total_protocols": len(protocols),
        "by_era": {"era1": 0, "era2": 0, "era3": 0},
        "unmapped_options": [],
        "era1_split_fallback": [],
    }

    # ── Pass 0: era2 -> canonical roster (reliable given/family split) + known klub vocabulary,
    # and remember the LATEST era2 meeting's own row order as the position reference era3 uses.
    persons: dict[str, dict[str, str]] = {}  # person_key -> {"given_name", "family_name"}
    known_klubs: set[str] = set()
    latest_era2_date = None
    latest_era2_order: list[str] = []  # person_key in Řádek order, from the latest era2 meeting

    for meeting, point_id, raw, era in protocols:
        if era != "era2":
            continue
        parsed = parse_era2(raw)
        order_this_meeting: list[str] = []
        for v in sorted(parsed["votes"], key=lambda v: (v["radek"] is None, v["radek"])):
            given = _strip_titles(v["title_given"])
            family = v["family"]
            key = _person_key(given, family)
            persons.setdefault(key, {"given_name": given, "family_name": family})
            known_klubs.add(v["klub"])
            order_this_meeting.append(key)
        if parsed["date"] and (latest_era2_date is None or parsed["date"] > latest_era2_date):
            latest_era2_date = parsed["date"]
            latest_era2_order = order_this_meeting

    if not latest_era2_order:
        raise RuntimeError("No era2 protocols found -- cannot build the canonical roster era1/era3 depend on")
    logging.info("Canonical roster: %d people (from era2, latest meeting date %s)", len(latest_era2_order), latest_era2_date)

    # ── Pass 1: era1 -> resolve names against the roster (heuristic split + fallback for
    # era1-only people never seen in era2).
    known_family_names = {p["family_name"] for p in persons.values()}

    # ── Pass 2 (combined with pass 1's loop): build the unified per-event vote list.
    events: list[dict[str, Any]] = []
    for meeting, point_id, raw, era in protocols:
        report["by_era"][era] += 1
        page_meta = _point_page_meta(raw_dir, point_id)

        if era == "era1":
            parsed = parse_era1(raw)
            resolved_votes = []
            for v in parsed["votes"]:
                given, family = _split_era1_name(v["name_raw"], known_family_names)
                key = _person_key(given, family)
                if key not in persons:
                    persons[key] = {"given_name": given, "family_name": family}
                    report["era1_split_fallback"].append({"name_raw": v["name_raw"], "given": given, "family": family})
                known_klubs.add(v["klub"])
                resolved_votes.append({"person_key": key, "klub": v["klub"], "option_raw": v["option_raw"]})
        elif era == "era2":
            parsed = parse_era2(raw)
            resolved_votes = []
            for v in parsed["votes"]:
                given = _strip_titles(v["title_given"])
                key = _person_key(given, v["family"])
                resolved_votes.append({"person_key": key, "klub": v["klub"], "option_raw": v["option_raw"]})
        else:  # era3
            roster = [{"title_given": persons[k]["given_name"], "family": persons[k]["family_name"]} for k in latest_era2_order]
            parsed = parse_era3(raw, roster, known_klubs)
            resolved_votes = []
            for v in parsed["votes"]:
                idx = v["roster_index"]
                if idx >= len(latest_era2_order):
                    logging.warning("Point %d: era3 row %d has no roster position (roster has %d people) -- skipped, not fabricated", point_id, idx, len(latest_era2_order))
                    continue
                key = latest_era2_order[idx]
                resolved_votes.append({"person_key": key, "klub": v["klub"], "option_raw": v["option_raw"]})

        options: list[dict[str, Any]] = []
        counts = {"yes": 0, "no": 0, "abstain": 0, "absent": 0, "not voting": 0}
        for v in resolved_votes:
            option = _OPTION_MAP.get(v["option_raw"])
            if option is None:
                report["unmapped_options"].append({"point_id": point_id, "option_raw": v["option_raw"], "person_key": v["person_key"]})
                logging.warning("Point %d: unmapped vote option %r for %s -- skipped, not fabricated", point_id, v["option_raw"], v["person_key"])
                continue
            counts[option] += 1
            options.append({"person_key": v["person_key"], "option": option, "klub": v["klub"]})

        yes, no = counts["yes"], counts["no"]
        result = "pass" if yes > no else ("fail" if no >= yes and (yes + no) > 0 else "fail")

        events.append(
            {
                "point_id": point_id,
                "meeting_id": meeting["meeting_id"],
                "date": parsed["date"] or meeting["date"],
                "time": parsed.get("time"),
                "era": era,
                "bod_number": page_meta["bod_number"],
                "press_number": page_meta["press_number"],
                "title": page_meta["title"],
                "proposer": page_meta["proposer"],
                "result": result,
                "counts": counts,
                "options": options,
            }
        )

    events.sort(key=lambda e: (e["date"] or "", e["time"] or "", e["point_id"]))
    return events, persons, report


def standardize(raw_dir: Path = _DEFAULT_RAW_DIR, out_dir: Path = _DEFAULT_OUT) -> dict[str, Any]:
    events, persons, report = resolve_all_events(raw_dir)

    # ── Build persons.csv / organizations.csv / memberships.csv ────────────────────────────────
    global_max_date = max((e["date"] for e in events if e["date"]), default=None)
    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for e in events:
        if not e["date"]:
            continue
        for o in e["options"]:
            k = o["person_key"]
            first_seen[k] = min(first_seen.get(k, e["date"]), e["date"])
            last_seen[k] = max(last_seen.get(k, e["date"]), e["date"])

    person_rows = []
    memberships = []
    for key in sorted(persons):
        if key not in first_seen:
            continue  # in roster but never actually cast a recorded vote -- don't fabricate a membership
        p = persons[key]
        person_id = f"plzen:person:{key}"
        person_rows.append(
            {
                "id": person_id,
                "name": f"{p['given_name']} {p['family_name']}".strip(),
                "given_name": p["given_name"],
                "family_name": p["family_name"],
                "identifiers": "[]",
                "sources": json.dumps([{"url": "https://usneseni.plzen.eu/", "note": "usneseni.plzen.eu ZMP vote protocols"}], ensure_ascii=False),
            }
        )
        end_date = "" if last_seen[key] == global_max_date else last_seen[key]
        memberships.append(
            {
                "id": f"plzen:membership:{key}:{ORG_ID.split(':', 2)[2]}",
                "person_id": person_id,
                "organization_id": ORG_ID,
                "start_date": first_seen[key],
                "end_date": end_date,
                "sources": json.dumps([{"url": "https://usneseni.plzen.eu/", "note": "start/end derived from first/last recorded vote"}], ensure_ascii=False),
            }
        )

    organization = {
        "id": ORG_ID,
        "name": ORG_NAME,
        "classification": "assembly",
        "identifiers": "[]",
        "sources": json.dumps([{"url": "https://usneseni.plzen.eu/", "note": "usneseni.plzen.eu"}], ensure_ascii=False),
    }

    # ── Build votes.csv / vote_events.json / motions.json ───────────────────────────────────────
    votes_rows = []
    vote_events = []
    motions = []
    for e in events:
        vote_event_id = f"plzen:vote-event:{e['point_id']}"
        motion_id = f"plzen:motion:{e['point_id']}"
        for o in e["options"]:
            votes_rows.append({"vote_event_id": vote_event_id, "voter_id": f"plzen:person:{o['person_key']}", "voter_type": "person", "option": o["option"]})

        identifier = f"{e['meeting_id']}/{e['bod_number']}" if e["bod_number"] else str(e["point_id"])
        start_date = f"{e['date']}T{e['time']}" if e["date"] and e["time"] else e["date"]
        sources = [{"url": f"https://usneseni.plzen.eu/ground/ground/point/{e['point_id']}", "note": f"era={e['era']}"}]
        vote_events.append(
            {
                "id": vote_event_id,
                "identifier": identifier,
                "motion_id": motion_id,
                "organization_id": ORG_ID,
                "start_date": start_date,
                "result": e["result"],
                "counts": [{"option": opt, "value": e["counts"][opt]} for opt in ("yes", "no", "abstain", "absent", "not voting")],
                "sources": sources,
                "extras": {"press_number": e["press_number"], "proposer": e["proposer"], "era": e["era"]},
            }
        )
        motions.append(
            {
                "id": motion_id,
                "identifier": identifier,
                "organization_id": ORG_ID,
                "date": e["date"],
                "text": e["title"],
                "result": e["result"],
                "sources": sources,
                "extras": {"press_number": e["press_number"]},
            }
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(person_rows).fillna("").to_csv(out_dir / "persons.csv", index=False, encoding="utf-8")
    pd.DataFrame([organization]).fillna("").to_csv(out_dir / "organizations.csv", index=False, encoding="utf-8")
    pd.DataFrame(memberships).fillna("").to_csv(out_dir / "memberships.csv", index=False, encoding="utf-8")
    pd.DataFrame(votes_rows).to_csv(out_dir / "votes.csv", index=False, encoding="utf-8")
    (out_dir / "vote_events.json").write_text(json.dumps(vote_events, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (out_dir / "motions.json").write_text(json.dumps(motions, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    logging.info("Wrote persons.csv (%d rows)", len(person_rows))
    logging.info("Wrote organizations.csv (1 row)")
    logging.info("Wrote memberships.csv (%d rows)", len(memberships))
    logging.info("Wrote votes.csv (%d rows)", len(votes_rows))
    logging.info("Wrote vote_events.json (%d records)", len(vote_events))
    logging.info("Wrote motions.json (%d records)", len(motions))
    logging.info("By era: %s", report["by_era"])
    logging.info("Unmapped options: %d, era1 split-fallback people: %d", len(report["unmapped_options"]), len(report["era1_split_fallback"]))

    report["persons_count"] = len(person_rows)
    report["memberships_count"] = len(memberships)
    report["votes_count"] = len(votes_rows)
    report["vote_events_count"] = len(vote_events)
    report["motions_count"] = len(motions)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", default=str(_DEFAULT_RAW_DIR))
    parser.add_argument("--out-dir", default=str(_DEFAULT_OUT))
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    report = standardize(Path(args.raw_dir), Path(args.out_dir))

    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
