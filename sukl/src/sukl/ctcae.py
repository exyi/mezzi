"""CTCAE vocabulary supplied in context and checked without rejecting final results."""

import csv
from pathlib import Path

from .schema import DrugExtraction


def load_terms(path: Path) -> set[str]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = csv.DictReader(stream)
        column = "CTCAE v6.0 MedDRA 28.0 Term"
        if column not in (rows.fieldnames or []):
            raise ValueError(f"{path}: missing {column!r} column")
        terms = {row[column].strip() for row in rows if row[column] and row[column].strip()}
    if not terms:
        raise ValueError(f"{path}: no CTCAE terms")
    return terms


def validation_warnings(output: DrugExtraction, terms: set[str]) -> list[str]:
    effects = [(f"side_effects[{i}]", effect) for i, effect in enumerate(output.side_effects)]
    for i, combination in enumerate(output.combinations):
        effects.extend((f"combinations[{i}].side_effects[{j}]", effect)
                       for j, effect in enumerate(combination.side_effects))
    return [f"{path}.ctcae: {effect.ctcae!r} is not an exact CTCAE v6 term from the supplied list."
            for path, effect in effects if effect.ctcae is not None and effect.ctcae not in terms]
