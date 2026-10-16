#!/usr/bin/env python3
"""Merge several index-text files (e.g. Czech -> English dictionaries) into one CSV.

Each input line looks like:  "13a SOMETHING"  or  "13 SOMETHING"

If the same number appears several times in one file, only one entry is kept:
  - prefer the entry without a trailing letter ("13" beats "13a")
  - otherwise the smallest letter ("13a" beats "13b")

Output: one column per input file, rows sorted by number, empty cells for
missing values.

Usage:
    python merge_translations.py file1.txt file2.txt -o output.csv
"""

import argparse
import csv
import re
from pathlib import Path

LINE_RE = re.compile(r"^(\d+)([a-z]?)\s+(.+)$", re.IGNORECASE)


def read_file(path: Path) -> dict[int, str]:
    """Return {number: text} with only the best entry kept per number."""
    entries: dict[int, tuple[str, str]] = {}  # number -> (letter, text)

    with path.open(encoding="utf-8") as f:
        for line in f:
            m = LINE_RE.match(line.strip())
            if not m:
                continue
            num, letter, text = int(m.group(1)), m.group(2).lower(), m.group(3)
            key = (1 if letter else 0, letter)  # no letter first, then a < b < ...
            if num not in entries or key < entries[num][0]:  # type: ignore
                entries[num] = (key, text)  # type: ignore

    return {num: text for num, (_, text) in entries.items()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge several index-text files into a CSV translation table."
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Input files (UTF-8, one '<id> <text>' per line)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("translations-merged.csv"),
        help="Output CSV file (default: translations-merged.csv)",
    )
    args = parser.parse_args()

    tables = [read_file(p) for p in args.files]
    all_numbers = sorted(set().union(*tables))

    languages = [(p.name).split("-")[0] for p in args.files]
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ID"] + languages)
        for num in all_numbers:
            row = []
            for lang_id, t in enumerate(tables):
                _v = t.get(num)
                if _v is None:
                    print(f"Missing value in table '{languages[lang_id]}' for {num}")
                else:
                    row.append(_v)
            writer.writerow([num] + row)

    print(f"Matched translations for {len(languages)} languages: {languages}")

if __name__ == "__main__":
    main()
