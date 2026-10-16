import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SymptomTranslation:
    """One row of the symptom translation table (ID + one column per language)."""

    id: int
    translations: dict[str, str]  # language code -> translated text

    def get(self, lang: str, default: str | None = None) -> str | None:
        return self.translations.get(lang, default)

    @property
    def languages(self) -> list[str]:
        return list(self.translations)


def load_translations(path: str | Path) -> dict[int, SymptomTranslation]:
    """Load the translations CSV; language columns are read from the header.

    Returns a dict keyed by numeric ID.
    """
    with open(path, encoding="utf8", newline="") as f:
        reader = csv.DictReader(f)
        # Everything except "ID" is treated as a language code
        languages = [
            col.strip() for col in reader.fieldnames or [] if col.strip() != "ID"
        ]

        result: dict[int, SymptomTranslation] = {}
        for row in reader:
            row = {k.strip(): v for k, v in row.items()}
            term_id = int(row["ID"])
            result[term_id] = SymptomTranslation(
                id=term_id,
                translations={
                    lang: row[lang].strip() for lang in languages if row.get(lang)
                },
            )
        return result
