# SUKL document extraction

Extract drug data using CSV metadata, composition, PIL, SPC and CTCAE v6. Each drug
has its own model conversation; batches can process several drugs concurrently.
PDFs are read directly from extracted folders. Pydantic AI provides document search
tools and native, strict JSON-schema output.

## Run

Requires Python 3.12+, uv and Poppler (pdftotext; pdftoppm for optional vision).
Dependencies are managed by the repository-root `pyproject.toml`; the commands
below can still be run from this directory.

```bash
uv sync
export API_KEY='your-key'
uv run sukl extract 0000009 --model gpt-6-luna-dz-eu
```

For a batch, put SUKL IDs in a UTF-8 text file and set the number of concurrent drugs:

```text
# ids.txt: one per line, or separated by whitespace, commas or semicolons
0000009
0000010
```

```bash
uv run sukl extract --ids-file ids.txt --parallelism 8 \
  --model gpt-6-luna-dz-eu --output-dir outputs --resume --trace
```

--parallel is an alias for --parallelism; the default is 1. IDs are normalized to
seven digits and deduplicated in input order. Blank lines and # comments are allowed.
Malformed or unknown codes are skipped with a warning; a list without any valid
codes stops before model calls. Provide either a
single positional code or --ids-file. --output selects a single-code output file;
--output-dir selects the directory for per-code files (default: outputs).

Workers overlap model calls while sharing the local PDF cache. Limits, validation,
messages and traces remain separate for each drug; trace lines include its SUKL code.
Successful results are saved immediately. A failed drug does not stop the other
workers; a batch with failures exits with status 1 after printing a summary.
Rerun with --resume to skip valid saved results and retry failed drugs. Resumed
results skip document preparation and model calls. --force regenerates all results.

The default model is gpt-6-luna; override it with --model or MODEL. The default
Responses API endpoint is https://apim-rakathon-q7146n.azure-api.net/openai/v1/.
The client sends the required api-key header. Override the base URL with --base-url
or OPENAI_BASE_URL. Environment variables must be exported; .env is not auto-loaded.

If a gateway only supports Chat Completions, disable reasoning for GPT-6 Luna tools:

```bash
uv run sukl extract 0000009 --model gpt-6-luna-dz-eu --api chat \
  --model-settings '{"openai_reasoning_effort":"none"}'
```

## Output

### Codex subscription

With `codex login` authenticated using ChatGPT, run the relevant test set with:

```bash
uv run sukl extract --ids-file relevant_ids.txt --api codex --model gpt-6-luna \
  --parallelism 2 --output-dir outputs-codex-luna --resume --trace
```

This uses the Codex CLI login instead of API_KEY. Each drug receives its full
primary document text and the extraction schema. Codex can query CSV metadata
through `sukl find`; the Pydantic AI tools are not exposed. API request, tool and
token limits do not apply to this backend. Vision and model-settings are unsupported.
Traces contain Codex events and validation warnings. Rerun the same command to
resume saved results.

outputs/0000009.json contains only these two lists. This is a shape example,
not an extraction from a specific drug:

```json
{
  "side_effects": [
    {
      "ctcae": "Nausea",
      "frequency": "common",
      "label": "Nevolnost",
      "action": "contact_doctor",
      "action_condition": {"patient_guide": "Pokud je vám výrazně nevolno", "min_severity": null}
    }
  ],
  "combinations": [
    {"drug": "Example drug", "effect": "Současné užívání zvyšuje riziko nežádoucích účinků.", "effect_type": "increased_toxicity", "side_effects": []},
    {"drug_class": "Example class", "effect": "Současné užívání snižuje účinnost léčby.", "effect_type": "antagonism", "side_effects": []}
  ]
}
```

Each side effect has five fields:

- ctcae: English CTCAE v6 term or null.
- frequency: one in N for an explicit point rate (5% becomes 20); otherwise a
  category (very_common, common, uncommon, rare, very_rare, not_known), or null.
  Rates must match the entry's severity and population. A grade-specific rate
  cannot become an overall incidence; use the source's overall category instead.
- label: Czech description, including any dose, population or severity conditions.
- action: continue, discontinue_treatment, contact_doctor, emergency, or null
  when the source gives no recommendation.
- action_condition: {"patient_guide": "..."} for a source-supported condition on
  the action; null when action is null. Include the severity threshold otherwise,
  with patient_guide null if there is no additional condition. Text addresses the patient
  in plain Czech, preserving timing, severity, dose and other qualifiers. For example,
  {"patient_guide": "Pokud jste zároveň těhotná"}, only when supported by the source.
  Different conditions requiring different actions stay in separate entries.
  `min_severity` is an inferred precautionary action threshold: default 3,
  lower to 2 for explicit extra attention or early warning signs, and to 1 for
  instructions to act at the first sign or regardless of severity. Explicit source
  thresholds take precedence. Null if grading is not meaningful. This does not
  authorize inventing or escalating the action. There are no FHIR conditions.

Actions use reaction-specific patient advice, rather than generic side-effect
reporting boilerplate or clinician-only treatment plans. Conditions describe only
when the action applies, without commands to stop treatment or contact a doctor.
Simultaneous instructions for the same reaction/condition use one entry with the
most urgent supported action; other required steps remain briefly in label.

Each combination names either drug or drug_class, has a Czech effect description
(including source-supported conditions and recommendations), an effect_type for
grouping, and side_effects with those same five fields. This preserves interactions such as
reduced efficacy even when no adverse reaction is named. No quotes, citations or
run metadata are required.

effect_type is one of:

- contraindicated: the source explicitly prohibits combined use.
- synergy: additive or enhanced therapeutic effect.
- antagonism: opposed or reduced therapeutic effect.
- increased_toxicity: increased risk or severity of adverse effects.
- increased_exposure: increased drug concentrations or exposure.
- decreased_exposure: decreased concentrations or exposure.
- changed_exposure: explicit pharmacokinetic interaction with unspecified or mixed direction.
- null: no clear supported category.

When several apply, prefer contraindicated, then increased_toxicity, otherwise
the most directly stated effect. The full effect description retains the details.
All patient guidance, labels and effect descriptions use patient-friendly Czech.

The schema lives in src/sukl/schema.py; instructions live in src/sukl/prompts.py.
NativeOutput(DrugExtraction, strict=True) sends native Structured Outputs settings:
text.format on Responses, or response_format on Chat Completions. JSON Schema
constrains structure, types and enums; it does not guarantee factual correctness.
See the [official Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

All CTCAE terms from CTCAEv6.csv are included in the initial prompt (850 unique
terms in the current file). ctcae values remain English term strings, not MedDRA
numeric codes. A local soft check flags any non-null term that is not an exact
match, including terms inside combinations. If warnings occur and another model
request is available, the model sees the warnings and produces one revised answer
with tools disabled. The revised answer may retain warnings; there is no further
soft validation retry. Remaining warnings appear on stderr and in --trace, outside
the drug JSON. With an exhausted request budget, warnings are reported immediately.
This checks vocabulary membership, not whether a mapping is medically correct.
Normal JSON Schema validation still applies to every answer.

The [ad hoc output review](docs/output-review.md) documents source-checked examples
behind the prompt's mapping, frequency, action and interaction rules. The prompt
includes a final consistency check using existing evidence, without an additional
model request. Prompt changes require a fresh output directory or --force to take
effect; --resume reuses saved results that match the schema.

Existing outputs are protected. Use --force to regenerate an old output (including
files without effect_type and action_condition). --resume skips an existing result that
matches the current schema; it does not compare sources or model settings.
--trace prints tool names, arguments, input/output character counts and binary
byte counts to stderr while running. It saves the full messages, a tool-call
summary, per-request token usage, initial validation feedback, remaining warnings,
limits and any error to
outputs/0000009.trace.json, including when a usage limit stops extraction.
Character counts measure tool payloads, not tokens; per-request token counts
come from the model provider. The drug result stays small.

```bash
uv run sukl extract 0000009 --model gpt-6-luna-dz-eu --request-limit 20 --trace
```

## Data and tools

```text
data/
  DLP20260925/  # extracted Windows-1250, semicolon-delimited CSVs
  PIL20260925/  # PDFs directly inside this folder
  SPC20260925/  # PDFs directly inside this folder
ctcae-v6.pdf
CTCAEv6.csv
```

The DLP folder must contain dlp_lecivepripravky.csv, dlp_nazvydokumentu.csv,
dlp_slozeni.csv and dlp_latky.csv. With multiple snapshots, pass --snapshot YYYYMMDD
before the subcommand. download-sukl.sh downloads and extracts the data.
Use --ctcae-csv PATH before the subcommand to select a different term CSV.

```bash
# Offline inspection; no API_KEY needed:
uv run sukl find ACYLCOFFIN
uv run sukl prepare 0000009 --output outputs/context.txt
uv run sukl schema
uv run sukl status

# Index all PDFs once for global text search; offline and resumable:
uv run sukl index
uv run sukl search 'Nausea' --kind CTCAE
```

The agent can find_drugs in all CSV metadata, search_documents in indexed PDF text,
and read_pages from any catalog PDF on demand. CTCAE and the target PIL/SPC are
indexed automatically. Global text search covers indexed PDFs only; other documents
can still be located by metadata and loaded directly. Text is cached in SQLite at
.cache/sukl/corpus.sqlite. --vision adds a page_image tool for ambiguous tables
(maximum four images per run).

Each run supplies the target drug's CSV metadata plus both its PIL and SPC text,
up to --context-chars (default 160000), followed by the complete CTCAE term list.
The character budget applies to PIL/SPC text; metadata and CTCAE terms are extra.
When all pages fit, no reading tools are needed. Clear CTCAE mappings use the
supplied list directly; definition searches are optional for ambiguous terms.
An answer without warnings can finish in one model request with zero tool calls.
The agent must fetch remaining page ranges before finishing while budget remains.
If the budget runs out first, it returns findings from supplied evidence and prints
a coverage warning; --trace records the unread ranges. Missing PIL/SPC inputs
stop extraction unless --allow-partial is set; missing inputs then appear as a
console warning and in the prompt. At least one target PDF is required. Legacy .doc
files and PDFs without text layers are not processed.

Run limits default to 20 model requests per drug, 400000 cumulative tokens and
24000 output tokens per response. A request can call multiple tools. The model sees
its remaining request budget, and the last request has tools disabled with explicit
instructions to return final JSON from available evidence. Set --request-limit to
change this budget; it includes the final request and validation retries.

There is no total tool-call cap by default. Optional --tool-limit caps actual tool
executions, including retries. If a parallel batch exceeds the remaining slots,
allowed calls run and the rest return a budget-exhausted message. Subsequent requests
have no tools and must return final JSON. Traces distinguish blocked calls from
actual executions (tool_executions). Request and token limits remain hard limits. See
[Pydantic AI usage limits](https://pydantic.dev/docs/ai/core-concepts/agent/#usage-limits).
Responses
use store=false and Pydantic AI replays reasoning/tool history locally.

## Tests

```bash
uv run python -m unittest discover -s tests -v
```

Tests use small real PDFs and mocked models/HTTP responses. They check native strict
JSON Schema on both APIs, tool and reasoning replay, the small result shape,
one-pass soft validation, bounded batch concurrency, trace isolation, failure
recovery, patient action conditions, page coverage, caching and optional images.
No external requests.
DSPy can be added later once there are labeled examples for prompt evaluation.

Classification and CSV metadata
-------------------------------
The LLM returns the interaction `class_id` directly, plus the target drug's
functional `classes`. It classifies by meaning using the supplied ATC vocabulary
and the fixed functional vocabulary in `schema.py` (CYP/P-gp effects, QT risk,
serotonergic activity, etc.). Named compounds use `substance:KOD_LATKY` verified
through the CSV lookup tool. Unsupported targets use `class_id: null`.
There is no name-matching resolver or intermediate resolved-drug schema.

Python adds CSV ATC ancestors and active substance IDs to `classes`, and attaches
`metadata` with original product/composition/document rows and decoded dictionaries.
The complete result uses `schema_version: 3` and preserves the model's `usage`.
`schema` describes the output; `--resume` validates it without rewriting files.
Previous normalized files are retained unchanged.

Match a combination when its non-null `class_id` occurs in another drug's
`classes`. For example `atc:C09` matches every product with that ATC ancestor;
`substance:12` matches products containing that active substance;
`group:qt_prolonging` matches products classified into that functional group.
This rule is also documented beside the schema and implemented by
`matching_combinations`. Matches are candidates: `effect` retains route, dose,
exclusions and required multidrug conditions. A null ID does not establish safety.
