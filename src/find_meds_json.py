#!/usr/bin/env python3
"""Extract prescribed drugs from FHIR bundles (DocumentReference attachments).

Usage:
    python extract_drugs.py bundle.json
    python extract_drugs.py a.json b.json dir_with_bundles/ "data/*.json"
    python extract_drugs.py data/ --prescriptions-only --dedupe -f csv -o drugs.csv
"""

import argparse
import base64
import binascii
import csv
import glob
import json
import re
import sys
from dataclasses import asdict, fields
from pathlib import Path
from collections.abc import Iterable, Iterator
import tqdm

from .prescription import DrugPrescription

LOINC_PRESCRIPTION = "57833-6"

LINE = re.compile(
    r"^(?P<sukl>\d{7})\s+(?P<product>.+?)\s+"
    r"(?P<form>[A-Z]{3}(?:\s[A-Z]{3})?)\s+"
    r"(?P<pack>\d+/\d+bal\.)\s*(?P<dosing>.*)$"
)


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
                )
            )
    return out


def dedupe(items: Iterable[DrugPrescription]) -> list[DrugPrescription]:
    """Same drug on the same day for the same patient counts once.

    Prescription documents are preferred over clinic reports that repeat them.
    """
    best: dict[tuple, DrugPrescription] = {}
    for d in items:
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
    cols = ["patient_id", "date", "sukl", "name", "strength", "form", "pack", "dosing"]
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
    args = ap.parse_args()

    items: list[DrugPrescription] = []
    failed = 0
    for path in iter_input_files(args.inputs):
        try:
            items += extract_drugs(
                load_bundle(path), str(path), args.prescriptions_only
            )
        except (OSError, json.JSONDecodeError) as e:
            failed += 1
            print(f"error: {path}: {e}", file=sys.stderr)

    if args.dedupe:
        items = dedupe(items)
    items.sort(key=lambda d: (d.patient_id or "", d.date or "", d.sukl))

    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="") as f:
            WRITERS[args.format](items, f)
    else:
        # sys.stdout.reconfigure(encoding="utf-8")
        WRITERS[args.format](items, sys.stdout)


if __name__ == "__main__":
    main()
