#!/usr/bin/env python3
"""Parse CTCAE v6.0 CSV into typed dataclasses."""

import csv
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable, Iterator
from typing import Optional
import json


_GRADE_PREFIX = "Grade "


@dataclass(frozen=True, slots=True)
class Grade:
    """Description of one CTCAE grade for a term. '-' means 'not applicable'."""

    level: int
    description: str | None  # None when the CSV cell is "-"

    @property
    def applicable(self) -> bool:
        return self.description is not None

    def to_dict(self) -> dict:
        return {"level": self.level, "description": self.description}

    @classmethod
    def from_dict(cls, data: dict) -> "Grade":
        return cls(
            level=int(data["level"]),
            description=data.get("description"),  # absent key -> None, same as null
        )


@dataclass
class CTCAETerm:
    """One row of the CTCAE v6.0 (MedDRA 28.0) terminology table."""

    llt_code: int
    soc: str  # System Organ Class
    term: str
    grades: tuple[Grade, ...]  # always 5 entries (levels 1-5)
    definition: str | None
    navigational_note: str | None
    change: str | None  # CTCAE v6.0 Change
    id: int | None = None  # Global ID

    def grade(self, level: int) -> Grade | None:
        """Return the grade with the given level (1-5), or None."""
        for g in self.grades:
            if g.level == level:
                return g
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "llt_code": self.llt_code,
            "soc": self.soc,
            "term": self.term,
            "grades": [g.to_dict() for g in self.grades],
            "definition": self.definition,
            "navigational_note": self.navigational_note,
            "change": self.change,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CTCAETerm":
        return cls(
            id=data.get("id"),
            llt_code=int(data["llt_code"]),
            soc=data["soc"],
            term=data["term"],
            grades=tuple(Grade.from_dict(g) for g in data["grades"]),
            definition=data.get("definition"),
            navigational_note=data.get("navigational_note"),
            change=data.get("change"),
        )

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)

    @classmethod
    def from_json(cls, raw: str) -> "CTCAETerm":
        return cls.from_dict(json.loads(raw))


class CTCAEDatabase:
    def __init__(self, records: Iterable[CTCAETerm]) -> None:
        self._records: dict[str, CTCAETerm] = {t.term.lower(): t for t in records}

    def __getitem__(self, key: str) -> Optional[CTCAETerm]:
        return self._records.get(key.lower())

    def __iter__(self) -> Iterator[CTCAETerm]:
        return (v for v in self._records.values())

    def __len__(self) -> int:
        return len(self._records)

    def to_json(self, **kwargs) -> str:
        return json.dumps(
            [
                t.to_dict()
                for t in sorted(self, key=lambda t: t.id if t.id is not None else -1)
            ],
            ensure_ascii=False,
            **kwargs,
        )

    def ctcae_names(self) -> list[str]:
        return list(self._records.keys())

    @classmethod
    def from_json(cls, raw: str) -> "CTCAEDatabase":
        return cls([CTCAETerm.from_dict(d) for d in json.loads(raw)])


# Column order as it appears in the CSV
_HEADER_TO_FIELD = {
    "CTCAE v6.0 MedDRA 28.0 LLT Code": "llt_code",
    "CTCAE v6.0 MedDRA 28.0 SOC": "soc",
    "CTCAE v6.0 MedDRA 28.0 Term": "term",
    "Definition": "definition",
    "Navigational Note": "navigational_note",
    "CTCAE v6.0 Change": "change",
}


def _clean(value: str) -> str | None:
    """Normalize a cell: strip whitespace, map '-' (and empty) to None."""
    value = (value or "").strip()
    return None if value in ("", "-") else value


def _parse_row(row: dict[str, str]) -> CTCAETerm:
    grades: list[Grade] = []
    for column, cell in row.items():
        header = column.strip()
        if header.startswith(_GRADE_PREFIX):
            level = int(header.removeprefix(_GRADE_PREFIX))
            grades.append(Grade(level=level, description=_clean(cell)))
    grades.sort(key=lambda g: g.level)

    if len(grades) != 5:
        raise ValueError(f"Expected 5 grade columns, found {len(grades)}: {row!r}")

    return CTCAETerm(
        llt_code=int(row["CTCAE v6.0 MedDRA 28.0 LLT Code"]),
        soc=row["CTCAE v6.0 MedDRA 28.0 SOC"].strip(),
        term=row["CTCAE v6.0 MedDRA 28.0 Term"].strip(),
        grades=tuple(grades),
        definition=_clean(row["Definition"]),
        navigational_note=_clean(row["Navigational Note"]),
        change=_clean(row["CTCAE v6.0 Change"]),
    )


def load_ctcae(path: str | Path) -> list[CTCAETerm]:
    """Read the CTCAE CSV file and return a list of CtcaeTerm dataclasses."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return [_parse_row(row) for row in reader]


if __name__ == "__main__":
    from load_translations import load_translations
    from load_mapping import load_mapping

    names = load_translations("assets/translations-merged.csv").values()
    mapping = load_mapping("assets/mapping-to-ctcae.txt")
    terms = load_ctcae("../sukl/CTCAEv6.csv")

    db = CTCAEDatabase(terms)

    total = 0
    for name in names:
        a = db[mapping.get(name.get("en").lower(), "")]  # type: ignore
        # print(name.get("en"))
        if a is not None:
            a.id = name.id
            total += 1

    # Clean data from un-matchable types
    clean_db = CTCAEDatabase([t for t in db if t.id is not None])
    print(f"Lowered number of tracked symptoms from {len(db)} to {len(names)}")
    print(f"Matched {len(clean_db)} records out of {len(names)}")

    with open("assets/serialized-ctcae-clean.json", "w", encoding="utf8") as f:
        f.write(clean_db.to_json())
