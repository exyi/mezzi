#!/usr/bin/env python3
"""Create a Czech side-effect questionnaire from one patient's FHIR bundles.

Run: python -m src.create_questionare_json patient.json --as-of 2026-10-17
Also accepts multiple files, directories, and glob patterns. JSON goes to stdout.
"""

import argparse
from datetime import date
import csv
import json
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.find_meds_json import (
    annotate_activity, dedupe, extract_drugs, iter_input_files, load_bundle,
)

from sukl.schema import SideEffect

ROOT = Path(__file__).resolve().parents[1]
PRECOMPUTED = ROOT / "sukl" / "precomputed-output"
REMAPPING = ROOT / "se-translation" / "assets" / "self-reportable-side-effect-remapping.csv"
CTCAE_METADATA = ROOT / "se-translation" / "assets" / "serialized-ctcae-clean.json"
ANNOTATIONS = ROOT / "se-translation" / "assets" / "ctcae-patient-sloplabels-cs.csv"


OPTIONS = [
    {"level": 0, "description": "Žádné obtíže"},
    {"level": 1, "description": "Mírné obtíže"},
    {"level": 2, "description": "Výrazné obtíže"},
]
ACTION_LABELS = {
    "continue": "Pokračujte v léčbě",
    "discontinue_treatment": "Přerušte léčbu",
    "contact_doctor": "Kontaktujte lékaře",
    "emergency": "Ihned vyhledejte lékařskou pomoc",
}


def patient_gender(bundle: dict) -> str | None:
    """Gender of the Patient resource in a FHIR bundle, if present."""
    for entry in bundle.get("entry", []):
        res = entry.get("resource", {})
        if res.get("resourceType") == "Patient":
            return res.get("gender")
    return None


def question_targets(effect: SideEffect, annotations: dict, rules: list[dict]) -> dict:
    term = (effect.ctcae or "").strip().casefold()
    if term in annotations:
        return {int(annotations[term]["id"]): "exact"}
    return {
        int(rule["target_question_id"]): "broader"
        for rule in rules
        if term == rule["source_term"].strip().casefold()
    }


def create_questionare_json(inputs: list[str], as_of: date) -> dict:
    """Return patient info and Czech questions with generic severity options.

    Output shape: {"patient": {"gender": ...}, "questions": [...]}.
    Uses the lookup's default 30-day fallback for PRN/unknown supply. Future
    prescriptions are excluded before selecting the latest prescription. Explicit
    non-active MedicationRequest statuses are excluded after that selection.
    Interaction-only effects are omitted: the prepared interaction names/classes
    do not provide reliable SUKL identifiers for matching a patient's regimen.
    """
    paths = list(iter_input_files(inputs))
    if not paths:
        raise ValueError("No input JSON files found")
    items = []
    gender = None
    for path in paths:
        bundle = load_bundle(path)
        found = patient_gender(bundle)
        if found and gender and found != gender:
            print(f"warning: conflicting patient gender {found!r} in {path}", file=sys.stderr)
        gender = gender or found
        items.extend(extract_drugs(bundle, str(path)))
    items = [item for item in items if item.date and date.fromisoformat(item.date) <= as_of]
    active = [
        item for item in annotate_activity(dedupe(items), as_of)
        if item.active and item.status in (None, "active")
    ]
    active.sort(key=lambda item: (item.sukl, item.date or "", item.doc_id))
    with ANNOTATIONS.open(encoding="utf-8-sig", newline="") as stream:
        annotations = {row["term"].strip().casefold(): row for row in csv.DictReader(stream)}
    by_id = {int(row["id"]): row for row in annotations.values()}
    metadata = {
        row["id"]: row
        for row in json.loads(CTCAE_METADATA.read_text(encoding="utf-8"))
    }
    with REMAPPING.open(encoding="utf-8-sig", newline="") as stream:
        rules = list(csv.DictReader(stream))
    grouped = {}
    seen_codes = set()
    for medication in active:
        code = str(medication.sukl).strip()
        code = code.zfill(7) if code.isdigit() else code
        if code in seen_codes:
            continue
        seen_codes.add(code)
        path = PRECOMPUTED / f"{code}.json"
        if not code.isdigit() or not path.is_file():
            print(f"warning: no prepared SUKL data for {code!r}", file=sys.stderr)
            continue
        prepared = json.loads(path.read_text(encoding="utf-8"))
        for raw_effect in prepared["side_effects"]:
            # fix older schema TODO: remove
            raw_effect = dict(raw_effect)
            if raw_effect.get("action") == "hospitalize_NOW":
                raw_effect["action"] = "emergency"
            if raw_effect.get("action_condition") is not None:
                raw_effect["action_condition"] = {
                    "min_severity": None,
                    **raw_effect["action_condition"],
                }
            effect = SideEffect.model_validate(raw_effect)
            for question_id, relationship in question_targets(effect, annotations, rules).items():
                if question_id not in by_id:
                    raise ValueError(f"Remapping references unknown question {question_id}")
                if question_id not in metadata:
                    raise ValueError(f"Missing CTCAE metadata for question {question_id}")
                tags = metadata[question_id]
                question = grouped.setdefault(question_id, {
                    "id": question_id,
                    "llt_code": tags["llt_code"],
                    "soc": tags["soc"],
                    "term": tags["term"],
                    "grades": [dict(option) for option in OPTIONS],
                    "definition": by_id[question_id]["label"],
                    "navigational_note": None,
                    "change": None,
                    "recommended_actions": [],
                    "sources": [],
                })
                source = {
                    "sukl": code, "medication": medication.name,
                    "effect": effect.label, "relationship": relationship,
                }
                if source not in question["sources"]:
                    question["sources"].append(source)
                if effect.action:
                    advice = {
                        "action": effect.action, "label": ACTION_LABELS[effect.action],
                        "condition": effect.action_condition.model_dump() if effect.action_condition else None,
                        "source": source,
                    }
                    if advice not in question["recommended_actions"]:
                        question["recommended_actions"].append(advice)
    return {
        "patient": {"gender": gender},
        "questions": [grouped[key] for key in sorted(grouped)],
    }



def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", help="FHIR JSON files, directories, or globs for one patient")
    parser.add_argument("--as-of", required=True, type=date.fromisoformat, help="Assumed current date (YYYY-MM-DD)")
    args = parser.parse_args()
    try:
        result = create_questionare_json(args.inputs, args.as_of)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f"error: {exc}\n")
    if not result:
        print(
            "warning: no active medications found - the questionnaire is empty",
            file=sys.stderr,
        )
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
