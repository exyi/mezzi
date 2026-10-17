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
uv run python -m src.create_questionare_json patient.json --as-of 2026-10-17 > side-effects.json
```

Accepts multiple FHIR Bundle files, directories, or quoted globs for one patient.
Uses `src/find_meds_json.py` to select estimated active medications across all
files, with its default 30-day fallback for as-needed or unknown supply. Future
prescriptions and explicitly non-active MedicationRequests are excluded.

Output is a JSON array matching the frontend question schema: `id`, `llt_code`,
`soc`, `term`, `grades`, `definition`, `navigational_note`, and `change`, plus
`recommended_actions` and `sources`. CTCAE tags come from
`serialized-ctcae-clean.json`; Czech question text in `definition` comes from
`ctcae-patient-sloplabels-cs.csv`. Grades are generated as 0 = Žádné obtíže,
1 = Mírné obtíže, 2 = Výrazné obtíže (none, mild, significant). These generic
levels are not CTCAE clinical grades. `navigational_note` and `change` are null.

Effects without a direct question use `self-reportable-side-effect-remapping.csv`
(columns `source_term,target_question_id`). Source CTCAE terms match ignoring
case and surrounding whitespace. Direct question annotations take precedence;
unmapped effects are omitted. Multiple effects targeting one question are merged.
Remapped sources are marked `broader` and retain their original descriptions.

Recommended actions include a stable action code, Czech label, original
`condition` (`patient_guide`, `min_severity`), and medication/effect provenance.
These are source-specific recommendations, not unconditional advice for any
positive answer. `min_severity` remains a CTCAE threshold; the generic options
are not automatically equated with CTCAE grades. Broader remaps
retain the original effect description so advice is not detached from its scope.
Missing prepared medications are reported on stderr. Prepared side effects use
the SUKL `SideEffect` Pydantic model, adapting legacy action names and missing
minimum severity before validation. Interaction-only effects are omitted.
