#!/usr/bin/env python3
"""Extract prescribed drugs from FHIR bundles (DocumentReference attachments).

Usage:
    python extract_drugs.py bundle.json
    python extract_drugs.py a.json b.json dir_with_bundles/ "data/*.json"
    python extract_drugs.py data/ --prescriptions-only --dedupe -f csv -o drugs.csv
    python extract_drugs.py data/ --active            # likely current meds only
    python extract_drugs.py a.json --active --as-of 2026-06-01
"""

import argparse
import base64
import binascii
import csv
import glob
import json
import re
import sys
from dataclasses import asdict, fields, replace
from datetime import date, timedelta
from pathlib import Path
from collections.abc import Iterable, Iterator
from tqdm import tqdm

from .prescription import DrugPrescription

LOINC_PRESCRIPTION = "57833-6"

LINE = re.compile(
    r"^(?P<sukl>\d{7})\s+(?P<product>.+?)\s+"
    r"(?P<form>[A-Z]{3}(?:\s[A-Z]{3})?)\s+"
    r"(?P<pack>\d.*?bal\.)\s*(?P<dosing>.*)$"
)

# heuristic for "is this med still active?"
#
#   active  <=>  latest prescription date + estimated days of supply >= reference date
#
# The days of supply are parsed from the prescription text itself:
#   * explicit duration         "2xd á 12h na 7dní"       -> 7 days
#   * units + daily rate         "90/2bal." + "1-0-0"      -> 2 packs of 90, 1/day
#   * as-needed (PRN) meds       "dp", "při nespavosti"    -> fallback window (default 90 d)

# "na 7dní", "na 30 d", "na 2 týdny"
EXPLICIT_DAYS = re.compile(r"\bna\s*(\d+)\s*d", re.IGNORECASE)
EXPLICIT_WEEKS = re.compile(r"\bna\s*(\d+)\s*t[ýy]d", re.IGNORECASE)
# "morning-noon-evening", e.g. "1-0-0" -> 1 unit/day
DAILY_SCHEME = re.compile(r"^\s*([0-3])\s*-\s*([0-3])\s*-\s*([0-3])\s*$")
# "2xd", "1xd", "2x/d", "2x denně" -> N units/day
PER_DAY = re.compile(r"(\d+)\s*x\s*(?:/?\s*d\b|denně)", re.IGNORECASE)
# pack quantity: leading integer of the pre-slash part
# ("90/2bal." -> 90, "14 II/1bal." -> 14, but "1X5ML/1bal." is a volume -> no count)
# II je nějaký identifikátor balení nebo co, takže nás to nezajímá
PACK_QTY = re.compile(r"^(\d+)\s*(?:[IVX]{1,4}\s*)?$")
# počet balení: číslice těsně před "bal." ("90/2bal." -> 2)
PACK_COUNT = re.compile(r"(\d+)\s*bal\.?$")
# as-needed markers: "dp", "d.p.", "dle potřeby", "při nespavosti", "u symptomů",
# and ceilings like "max 3x/d" which only appear on PRN meds
PRN = re.compile(
    r"\bd\.?\s?p\.?(?=\s|$)|\bdp\b|dle potřeby|u symptom|při |max\s+\d+\s*x",
    re.IGNORECASE,
)


def parse_pack_qty(pack: str) -> int | None:
    """Total units, e.g. '90/2bal.' -> 180 (2 balení po 90 ks)."""
    pre = pack.split("/")[0].strip()
    mq = PACK_QTY.match(pre)
    mc = PACK_COUNT.search(pack)
    if not mq:
        return None
    return int(mq.group(1)) * (int(mc.group(1)) if mc else 1)


def parse_daily_rate(dosing: str) -> int | None:
    """Units per day implied by the dosing text."""
    m = DAILY_SCHEME.match(dosing)
    if m:
        return sum(int(g) for g in m.groups())
    m = PER_DAY.search(dosing)
    if m:
        return max(1, int(m.group(1)))
    return None


def estimate_supply_days(pack: str, dosing: str) -> tuple[int | None, bool]:
    """Estimate how many days one prescription lasts.

    Returns (supply_days, is_prn); supply_days is None when it cannot be
    determined from pack + dosing (caller should use a fallback window).
    """
    is_prn = bool(PRN.search(dosing))
    m = EXPLICIT_DAYS.search(dosing)
    if m:
        return int(m.group(1)), is_prn
    m = EXPLICIT_WEEKS.search(dosing)
    if m:
        return 7 * int(m.group(1)), is_prn

    qty = parse_pack_qty(pack)
    rate = parse_daily_rate(dosing)
    if qty and rate:
        return (qty + rate - 1) // rate, is_prn  # ceil
    return None, is_prn


def decode_text(res: dict) -> str:
    """Decode the first text/plain attachment of a DocumentReference."""
    for item in res.get("content", []):
        att = item.get("attachment", {})
        if att.get("contentType") == "text/plain" and "data" in att:
            try:
                return base64.b64decode(att["data"]).decode("utf-8")
            except (binascii.Error, UnicodeDecodeError):
                return ""
    return ""


def _codings(res: dict) -> list[dict]:
    return res.get("type", {}).get("coding", [])


def _is_prescription(res: dict) -> bool:
    return any(c.get("code") == LOINC_PRESCRIPTION for c in _codings(res))


def _doc_type(res: dict) -> str | None:
    for c in _codings(res):
        if "unis-typ-zaznamu" in c.get("system", ""):
            return c.get("code")
    return None


def _split_product(product: str) -> tuple[str, str | None]:
    name, _, strength = product.partition("/")
    return name.strip(), (strength.strip() or None)


def _med_requests(
    resources: list[dict], patient_id: str | None, source_file: str | None
) -> list[DrugPrescription]:
    """Turn MedicationRequest resources into DrugPrescription items."""
    out: list[DrugPrescription] = []
    for res in resources:
        if res.get("resourceType") != "MedicationRequest":
            continue
        concept = res.get("medicationCodeableConcept", {})
        codings = concept.get("coding", [])
        sukl = next(
            (c.get("code") for c in codings if "sukl" in c.get("system", "")),
            None,
        )
        sukl = sukl or next((c.get("code") for c in codings), "") or concept.get(
            "text", ""
        )
        dosing = " ".join(
            d.get("text", "") for d in res.get("dosageInstruction", [])
        ).strip()
        supply_days, is_prn = estimate_supply_days("", dosing)
        out.append(
            DrugPrescription(
                patient_id=patient_id,
                doc_id=res.get("id", ""),
                doc_type=None,
                is_prescription=True,
                date=(res.get("authoredOn") or "")[:10] or None,
                sukl=sukl,
                product=concept.get("text", ""),
                name=concept.get("text", ""),
                strength=None,
                form="",
                pack="",
                dosing=dosing,
                source_file=source_file,
                status=res.get("status"),
                supply_days=supply_days,
                is_prn=is_prn,
            )
        )
    return out


def extract_drugs(
    bundle: dict, source_file: str | None = None, prescriptions_only: bool = False
) -> list[DrugPrescription]:
    entries = bundle.get("entry", [])
    resources = [e.get("resource", {}) for e in entries]

    patient_id = next(
        (r.get("id") for r in resources if r.get("resourceType") == "Patient"), None
    )

    out: list[DrugPrescription] = []
    for res in resources:
        if res.get("resourceType") != "DocumentReference":
            continue
        is_rx = _is_prescription(res)
        if prescriptions_only and not is_rx:
            continue

        for line in decode_text(res).splitlines():
            m = LINE.match(line.strip())
            if not m:
                continue
            d = m.groupdict()
            name, strength = _split_product(d["product"])
            supply_days, is_prn = estimate_supply_days(d["pack"], d["dosing"])
            out.append(
                DrugPrescription(
                    patient_id=patient_id,
                    doc_id=res.get("id", ""),
                    doc_type=_doc_type(res),
                    is_prescription=is_rx,
                    date=(res.get("date") or "")[:10] or None,
                    sukl=d["sukl"],
                    product=d["product"],
                    name=name,
                    strength=strength,
                    form=d["form"],
                    pack=d["pack"],
                    dosing=d["dosing"].strip(),
                    source_file=source_file,
                    supply_days=supply_days,
                    is_prn=is_prn,
                )
            )

    # structured MedicationRequests are prescriptions too, include them always
    out += _med_requests(resources, patient_id, source_file)
    return out


def _drug_key(d: DrugPrescription) -> tuple[str | None, str]:
    """Keyed by product name rather than SUKL code because the same drug in a
    different pack size carries a different SUKL code (e.g. DASSELTA
    0168837 50/1bal. vs 0168838 90/2bal.).
    """
    return (d.patient_id, (d.name or d.sukl or "").upper())


def annotate_activity(
    items: list[DrugPrescription], as_of: date, prn_window_days: int = 30
) -> list[DrugPrescription]:
    """Mark each prescription with its estimated 'active until' date.

    A drug is considered active when the *latest* prescription for it plus
    the estimated days of supply reaches `as_of`. Older prescriptions of the
    same drug are superseded and marked inactive. As-needed (PRN) meds and
    meds with unparseable dosing are assumed to last `prn_window_days`.
    """
    latest: dict[tuple, DrugPrescription] = {}
    for d in items:
        if d.date is None:
            continue
        key = _drug_key(d)
        cur = latest.get(key)
        if cur is None or (d.date, d.is_prescription) > (cur.date, cur.is_prescription):
            latest[key] = d

    out: list[DrugPrescription] = []
    for d in items:
        if d is not latest.get(_drug_key(d)) or not d.date:
            # not latest or without date
            out.append(replace(d, active=False, active_until=None))
            continue
        if d.status == "active":  # FHIR says so explicitly (asi neexistuje v našich datech ale hopfully in the future)
            out.append(replace(d, active=True, active_until=as_of.isoformat()))
            continue
        # PRN meds deplete unpredictably -> assume the fallback window
        supply = d.supply_days if d.supply_days is not None and not d.is_prn else prn_window_days
        until = (date.fromisoformat(d.date) + timedelta(days=supply)).isoformat()
        out.append(replace(d, active_until=until, active=as_of.isoformat() <= until))
    return out


def dedupe(items: Iterable[DrugPrescription]) -> list[DrugPrescription]:
    """Same drug on the same day for the same patient counts once.

    Prescription documents are preferred over clinic reports that repeat them.
    """
    best: dict[tuple, DrugPrescription] = {}
    for d in items:
        # hopefully no more than 1 of the same subscription per day
        key = (d.patient_id, d.sukl, d.date)
        if key not in best or (d.is_prescription and not best[key].is_prescription):
            best[key] = d
    return list(best.values())


# ---------------------------------------------------------------- I/O


def iter_input_files(patterns: Iterable[str]) -> Iterator[Path]:
    """Expand files, directories (*.json, recursive) and glob patterns."""
    seen: set[Path] = set()
    for pat in patterns:
        p = Path(pat)
        if p.is_dir():
            matches = sorted(p.rglob("*.json"))
        elif p.is_file():
            matches = [p]
        else:
            matches = sorted(Path(x) for x in glob.glob(pat, recursive=True))
        if not matches:
            print(f"warning: nothing matched {pat!r}", file=sys.stderr)
        for m in matches:
            rp = m.resolve()
            if rp not in seen and m.is_file():
                seen.add(rp)
                yield m


def load_bundle(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- output


def write_table(items: list[DrugPrescription], out) -> None:
    cols = ["patient_id", "date", "sukl", "name", "strength", "form", "pack", "dosing", "supply_days", "active_until", "active" ]
    rows = [[str(getattr(d, c) or "") for c in cols] for d in items]
    widths = [
        max(len(c), *(len(r[i]) for r in rows)) if rows else len(c)
        for i, c in enumerate(cols)
    ]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()
    print(line(cols), file=out)
    print(line(["-" * w for w in widths]), file=out)
    for r in rows:
        print(line(r), file=out)


def write_csv(items: list[DrugPrescription], out) -> None:
    w = csv.DictWriter(out, fieldnames=[f.name for f in fields(DrugPrescription)])
    w.writeheader()
    for d in items:
        w.writerow(asdict(d))


def write_json(items: list[DrugPrescription], out) -> None:
    json.dump([asdict(d) for d in items], out, ensure_ascii=False, indent=2)
    out.write("\n")


def write_jsonl(items: list[DrugPrescription], out) -> None:
    for d in items:
        out.write(json.dumps(asdict(d), ensure_ascii=False) + "\n")


WRITERS = {
    "table": write_table,
    "csv": write_csv,
    "json": write_json,
    "jsonl": write_jsonl,
}


# ---------------------------------------------------------------- CLI


def main():
    ap = argparse.ArgumentParser(
        description="Extract prescribed drugs from FHIR bundles."
    )
    ap.add_argument(
        "inputs", nargs="+", help="JSON file(s), directory(ies) or glob pattern(s)"
    )
    ap.add_argument(
        "-f",
        "--format",
        choices=WRITERS,
        default="table",
        help="output format (default: table)",
    )
    ap.add_argument("-o", "--output", help="write to this file instead of stdout")
    ap.add_argument(
        "--prescriptions-only",
        action="store_true",
        help="only use documents with LOINC 57833-6 (skip clinic reports)",
    )
    ap.add_argument(
        "--dedupe",
        action="store_true",
        help="collapse the same drug on the same date per patient",
    )
    ap.add_argument(
        "--active",
        action="store_true",
        help="only show medications likely still being taken "
        "(latest Rx date + estimated supply >= reference date)",
    )
    ap.add_argument(
        "--as-of",
        help="reference date for --active, YYYY-MM-DD (default: bundle timestamp)",
    )
    ap.add_argument(
        "--prn-window",
        type=int,
        default=90,
        metavar="DAYS",
        help="assumed supply window for as-needed meds or unparseable dosing "
        "(default: 90 days)",
    )
    ap.add_argument(
        "--since",
        metavar="DATE",
        help="only include prescriptions dated on/after YYYY-MM-DD",
    )
    args = ap.parse_args()

    items: list[DrugPrescription] = []
    failed = 0
    for path in tqdm(iter_input_files(args.inputs)):
        try:
            bundle = load_bundle(path)
            bundle_items = extract_drugs(
                bundle, str(path), args.prescriptions_only
            )
            # reference date for activity analysis: --as-of > bundle timestamp
            ref = args.as_of or (bundle.get("timestamp") or "")[:10]
            try:
                as_of = date.fromisoformat(ref)  # "" raises ValueError
            except ValueError:
                as_of = None
                if args.active:
                    print(f"warning: {path}: no reference date for --active (need bundle timestamp or --as-of)", file=sys.stderr)
            if as_of is not None:
                bundle_items = annotate_activity(bundle_items, as_of)
            items += bundle_items
        except (OSError, json.JSONDecodeError) as e:
            failed += 1
            print(f"error: {path}: {e}", file=sys.stderr)

    if args.dedupe:
        items = dedupe(items)
    if args.since:
        items = [d for d in items if d.date and d.date >= args.since]
    if args.active:
        items = [d for d in items if d.active]
    items.sort(key=lambda d: (d.patient_id or "", d.date or "", d.sukl))

    if args.output is not None:
        Path(args.output).parent.mkdir(exist_ok=True, parents=True)

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            WRITERS[args.format](items, f)
    else:
        WRITERS[args.format](items, sys.stdout)


if __name__ == "__main__":
    main()
