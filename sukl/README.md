# SUKL document extraction

Process one SUKL code at a time using CSV metadata, composition, PIL, SPC and CTCAE
v6. PDFs are read directly from extracted folders. Pydantic AI provides document
search tools and native, strict JSON-schema output.

## Run

Requires Python 3.14+, uv and Poppler (pdftotext; pdftoppm for optional vision).

```bash
uv sync
export API_KEY='your-key'
uv run sukl extract 0000009 --model gpt-6-luna-dz-eu
```

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

outputs/0000009.json contains only these two lists. This is a shape example,
not an extraction from a specific drug:

```json
{
  "side_effects": [
    {"ctcae": "Nausea", "frequency": "common", "label": "Nauzea", "action": null}
  ],
  "combinations": [
    {"drug": "Example drug", "side_effects": []},
    {"drug_class": "Example class", "side_effects": []}
  ]
}
```

Each side effect has exactly four fields:

- ctcae: English CTCAE v6 term or null.
- frequency: one in N for an explicit point rate (5% becomes 20); otherwise a
  category (very_common, common, uncommon, rare, very_rare, not_known), or null.
- label: Czech description, including any dose, population or severity conditions.
- action: continue, discontinue_treatment, contact_doctor, hospitalize_NOW, or null
  when the source gives no recommendation.

Each combination names either drug or drug_class and contains side_effects with
those same four fields. No quotes, citations or run metadata are required.

The schema lives in src/sukl/schema.py; instructions live in src/sukl/prompts.py.
NativeOutput(DrugExtraction, strict=True) sends native Structured Outputs settings:
text.format on Responses, or response_format on Chat Completions. JSON Schema
constrains structure, types and enums; it does not guarantee factual correctness.
See the [official Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Existing outputs are protected. Use --force to regenerate an old output (including
files from the previous larger schema). --resume skips an existing result that
matches the current schema; it does not compare sources or model settings.
--trace writes prompts, tool calls, model name and usage to a separate
outputs/0000009.trace.json file, leaving the result small.

## Data and tools

```text
data/
  DLP20260925/  # extracted Windows-1250, semicolon-delimited CSVs
  PIL20260925/  # PDFs directly inside this folder
  SPC20260925/  # PDFs directly inside this folder
ctcae-v6.pdf
```

The DLP folder must contain dlp_lecivepripravky.csv, dlp_nazvydokumentu.csv,
dlp_slozeni.csv and dlp_latky.csv. With multiple snapshots, pass --snapshot YYYYMMDD
before the subcommand. download-sukl.sh downloads and extracts the data.

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

All primary text is supplied initially up to --context-chars (default 160000).
The agent must fetch remaining page ranges before finishing. Missing PIL/SPC inputs
stop extraction unless --allow-partial is set; missing inputs then appear as a
console warning and in the prompt. At least one target PDF is required. Legacy .doc
files and PDFs without text layers are not processed.

Run limits default to 25 requests, 70 tool calls, 400000 cumulative tokens and 24000
output tokens per response; corresponding CLI flags are configurable. Responses
use store=false and Pydantic AI replays reasoning/tool history locally.

## Tests

```bash
uv run python -m unittest discover -s tests -v
```

Tests use small real PDFs and mocked models/HTTP responses. They check native strict
JSON Schema on both APIs, tool and reasoning replay, the small result shape,
separate traces, page coverage, caching and optional images. No external requests.
DSPy can be added later once there are labeled examples for prompt evaluation.
