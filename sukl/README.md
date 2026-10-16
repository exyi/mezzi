# SUKL document extraction

Extract one SUKL product at a time with Pydantic AI, using CSV metadata, composition,
the patient leaflet (PIL), the summary of product characteristics (SPC), and CTCAE v6.
The default deployment is `gpt-6-luna`; both model and gateway are configurable.

## Quick start

Requires Python 3.14+, `uv`, and Poppler (`pdftotext`; `pdftoppm` for optional vision).
On Debian/Ubuntu, Poppler is provided by `poppler-utils`.

```bash
uv sync

# These commands are offline and do not need API_KEY.
uv run sukl status
uv run sukl find ACYLCOFFIN
uv run sukl prepare 0000009 --output outputs/0000009-context.txt
uv run sukl schema

# Export your actual gateway key in your shell. .env is not automatically loaded.
export API_KEY='your-key'
export MODEL='gpt-6-luna'
uv run sukl extract 0000009
```

The result is written atomically to `outputs/0000009.json`. Existing results are
protected: use `--resume` to reuse an unchanged validated result or `--force` to
replace it after a successful new run. Resume checks metadata, all PDF source
signatures, schema, instructions and model settings. Warming the text cache alone
does not invalidate it. Changes to code should be accompanied by a prompt/schema
version bump when they change extraction behavior.

```bash
uv run sukl extract 0000009 --model gpt-6-luna --resume
uv run sukl extract 0000009 --vision --trace --output outputs/0000009-vision.json
uv run sukl extract 0000009 --model-settings '{"openai_reasoning_effort":"low"}'
```

The gateway must support Chat Completions and function calling. `--vision` additionally
requires image input support. The client uses the supplied `/openai/v1/` base URL and
`default_headers={"api-key": os.environ["API_KEY"]}`. It also supplies the key through
the OpenAI SDK's standard `api_key` argument. Override the URL with `--base-url` or
`OPENAI_BASE_URL`. No deployment discovery or live calls happen during preparation.

## Data layout and search

```text
data/
  DLP20260925/              # extracted semicolon-delimited Windows-1250 CSVs
    dlp_lecivepripravky.csv
    dlp_nazvydokumentu.csv
    dlp_slozeni.csv
    dlp_latky.csv
    ...
  PIL20260925/              # extracted PDFs, directly inside this folder
  SPC20260925/              # extracted PDFs, directly inside this folder
ctcae-v6.pdf
```

Selection uses the exact SUKL code, retaining leading zeros. Package codes sharing
a PDF share its cached text; extraction still targets the requested strength, route
and formulation. With multiple snapshots, select one explicitly. Global options go
**before** the subcommand:

```bash
uv run sukl --snapshot 20260925 --data data --ctcae ctcae-v6.pdf extract 0000009
```

Extract the downloaded archives before running the pipeline (`download-sukl.sh`
already does this). The loader reads PDFs directly from these folders.
`pdftotext -layout` preserves table spacing and page boundaries. The SQLite cache at
`.cache/sukl/corpus.sqlite` stores page text and overlapping FTS5 chunks; changed or
removed sources invalidate cached text. PDFs without a text layer fail explicitly.
Legacy `.doc` and other non-PDF files are counted as unsupported. Missing PIL/SPC
documents stop extraction unless `--allow-partial` is set, in which case the output
records the missing inputs. At least one target PDF is always required.

Preparation indexes the target's PIL/SPC and CTCAE. To make **all other PDFs**
searchable by text, build the corpus index once. This is offline, resumable and does
not spend LLM tokens; the full corpus can take considerable time and disk space.

```bash
uv run sukl index                         # all PDFs
uv run sukl index --kind SPC --limit 100  # up to 100 NEW PDFs, skipping cached PDFs
uv run sukl search 'krvácení' --kind SPC
uv run sukl search 'Nausea' --kind CTCAE
uv run sukl search 'interakce' --document SPC:SPC223083.pdf
```

The agent has these bounded tools:

| Tool | Purpose |
| --- | --- |
| `find_drugs` | Search all CSV metadata by brand, substance, INN, English name or ATC, returning document IDs. |
| `search_documents` | Search indexed text with accent-insensitive AND keywords; optionally load a specified PDF. Reports coverage. |
| `read_pages` | Read any catalog PDF on demand, including unindexed interacting drugs; follow offsets on long pages. |
| `page_image` | With `--vision`, render at most four pages to inspect ambiguous tables. |

Global text search covers indexed PDFs only. Metadata lookup and direct page access
work across the entire catalog even before full indexing. Other-drug documents are
contextual evidence and cannot establish a reaction, incidence or action for the
target drug. Search results include original page numbers and source IDs.

## Output contract

Edit [`src/sukl/schema.py`](src/sukl/schema.py) to evolve the domain contract and
[`src/sukl/prompts.py`](src/sukl/prompts.py) to change instructions. The output schema
is passed directly to the model as a Pydantic AI output tool; no separate JSON parser
or duplicated hand-written JSON schema is required.

The requested `side_effects` and `combinations` lists are preserved, with a few changes:

- `frequency` preserves the category and original text, with optional population
  qualifiers. A category such as “common” is a range, not an exact “one in 20”.
  Only an explicit point rate gets `exact_probability`; Python's `Frequency.one_in`
  gives its reciprocal (`0.05` → `20`). `Frequency.interval` exposes category bounds
  and their inclusivity. These convenience properties are not extra model-generated
  JSON fields. Unknown frequency is `null` or the explicit `not_known` category.
- `ctcae` is an exact English CTCAE v6 term or `null`, with reference citations and
  an optional mapping note. The source's Czech symptom label is retained. CTCAE
  severity grades are not treated as frequency or treatment recommendations.
- `action` is one of the requested four values or `null`. It requires explicit
  target-document evidence, and `action_condition` preserves severity and symptom
  qualifiers. `hospitalize_NOW` represents source-directed emergency help; the source
  wording should be reviewed before interpreting it as a requirement for admission.
  No action in the source means unknown, never an inferred `continue`.
- Each claim has page citations and verbatim supporting quotes. Interactions require
  exactly one of `drug` or `drug_class`, plus an `effect` and optional source
  `recommendation`. Their `side_effects` can be empty when the interaction only changes
  exposure or efficacy.
- `uncertainties` records conflicts, ambiguity, incomplete inputs and unmapped terms.

Each run artifact wraps the extraction with input metadata, model/settings, token
usage, source signatures and extracted-text hashes, prompt/schema hashes, and
`review_status: "unreviewed"`. `--trace` additionally retains the full conversation
for debugging and future evaluation; image tool outputs can make it large.
Credentials are read from the environment and are not included in run artifacts.

## Validation and model evaluation

All target pages enter the initial prompt up to `--context-chars` (default 160,000
source-text characters, not tokens). Remaining text is explicitly listed, and the
validator rejects final output until every target page range has been supplied
through the prompt or page tools. This prevents silent truncation; it does not prove
the model attended to every reaction.

Pydantic validates the structure. A second validator checks quote existence after
whitespace normalization, target-document support, action citation sources and
exact CTCAE term headings on the cited pages. Validation failures are returned to
the model for bounded repair. These checks do **not** prove semantic entailment,
complete recall, correct table interpretation or clinical correctness. Audit the
evidence before using actions or frequencies in a patient-facing product.

Defaults cap a run at 25 model requests, 70 tool calls, 400,000 cumulative tokens and
24,000 output tokens per response; all are configurable. Token usage limits are
checked around model requests and can be exceeded by the last response. HTTP
timeouts and three SDK retries bound transient failures. No temperature is forced,
so reasoning models can use their supported defaults. Large documents may require
larger budgets; errors leave a previous successful artifact intact.

```bash
uv run python -m unittest discover -s tests -v
```

Tests use small real PDFs and CSVs, plus Pydantic AI `FunctionModel` responses.
They cover extraction/cache invalidation, exact product identity, citations, CTCAE,
frequency semantics, tool retrieval, validation repair and artifact writing, without
external requests. A live Azure run still requires `API_KEY` and an available model
deployment.

DSPy remains installed but is intentionally outside the first extraction path.
The useful next step is a small adjudicated set of drugs with complete reaction and
interaction labels. Evaluate event recall, unsupported claims, CTCAE mapping,
frequency correctness, conditional actions, latency and token cost; then use those
labels to optimize prompts/model choice with DSPy. Optimizing on successful JSON
validation alone would reward unsupported but well-formed answers.

Provider integration follows the installed Pydantic AI API and its
[compatible API documentation](https://pydantic.dev/docs/ai/models/compatible-apis/).
