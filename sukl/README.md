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
    {"drug": "Example drug", "effect": "Zvyšuje riziko toxicity.", "side_effects": []},
    {"drug_class": "Example class", "effect": "Snižuje účinnost léčby.", "side_effects": []}
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

Each combination names either drug or drug_class, has a Czech effect description
(including source-supported conditions and recommendations), and contains
side_effects with those same four fields. This preserves interactions such as
reduced efficacy even when no adverse reaction is named. No quotes, citations or
run metadata are required.

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

Existing outputs are protected. Use --force to regenerate an old output (including
files from the previous larger schema). --resume skips an existing result that
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
one-pass soft validation, separate traces, page coverage, caching and optional
images. No external requests.
DSPy can be added later once there are labeled examples for prompt evaluation.
