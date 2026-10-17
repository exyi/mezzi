# rakaslop
# rakaslop

## questionnaire/
Patient questionnaire web app (FastAPI + Svelte). See [questionnaire/README.md](questionnaire/README.md).

## Patient medication side effects

Install all Python dependencies from the repository root with Python 3.12+:

```sh
uv sync
```

Run from the repository root:

```sh
uv run python -m src.aggregate_side_effects patient.json --as-of 2026-10-17 > side-effects.json
```

Accepts multiple FHIR Bundle files, directories, or quoted globs for one patient.
Uses `src/find_meds_json.py` to select estimated active medications across all
files, with its default 30-day fallback for as-needed or unknown supply. Future
prescriptions and explicitly non-active MedicationRequests are excluded.

Output includes active `medications`, grouped potential `side_effects`, and
`missing_precomputed` medications. Each effect has a CSV `annotation` (id, term,
and Czech patient question), or null when no match exists, and `sources` retaining
each medication's frequency, original label, action, and action condition.
Prepared data and annotations are resolved relative to the script automatically.
These are potential medication effects, not observed patient symptoms.
Interaction-only effects are omitted because prepared interaction names/classes
do not reliably identify the patient's SUKL-coded medications.
