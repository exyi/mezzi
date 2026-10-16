from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import TypeAdapter
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext, UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from .corpus import Corpus, normalize_quote
from .prompts import INSTRUCTIONS, PROMPT_VERSION
from .schema import Citation, DrugExtraction

DEFAULT_BASE_URL = "https://apim-rakathon-q7146n.azure-api.net/openai/v1/"
DEFAULT_MODEL = "gpt-6-luna"


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    context_chars: int = 160_000
    request_limit: int = 25
    tool_limit: int = 70
    total_tokens: int = 400_000
    max_output_tokens: int = 24_000
    vision: bool = False
    model_settings: dict = field(default_factory=dict)


@dataclass
class Dependencies:
    corpus: Corpus
    code: str
    primary_ids: set[str]
    missing: list[str]
    # Track exactly which page ranges were supplied, including partial long pages.
    exposed: dict[tuple[str, int], list[tuple[int, int]]] = field(default_factory=dict)
    touched: set[str] = field(default_factory=set)
    image_count: int = 0

    def expose(self, document_id: str, page: int, start: int, end: int) -> None:
        self.touched.add(document_id)
        self.exposed.setdefault((document_id, page), []).append((start, end))

    def unread(self) -> list[dict]:
        unread = []
        for doc_id in sorted(self.primary_ids):
            for page in self.corpus.pages(doc_id):
                end = 0
                for start, stop in sorted(self.exposed.get((doc_id, page["page"]), [])):
                    if start > end:
                        break
                    end = max(end, stop)
                if end < len(page["text"]):
                    unread.append({"document_id": doc_id, "page": page["page"], "start_char": end})
        return unread


def prepare(corpus: Corpus, code: str, settings: Settings, allow_partial: bool = False) -> tuple[str, Dependencies]:
    code = corpus.catalog.code(code)
    docs, missing = corpus.catalog.drug_documents(code)
    if missing and not allow_partial:
        raise ValueError(f"Missing target documents: {missing}. Use --allow-partial explicitly to proceed.")
    if not docs:
        raise ValueError("At least one target PIL/SPC PDF is required")
    deps = Dependencies(corpus, code, {d.id for d in docs}, missing)
    corpus.ensure("CTCAE:ctcae-v6.pdf")
    deps.touched.add("CTCAE:ctcae-v6.pdf")
    all_pages = {doc.id: corpus.pages(doc.id) for doc in docs}
    manifest = [{"document_id": doc.id, "pages": len(all_pages[doc.id])} for doc in docs]
    metadata = json.dumps(corpus.catalog.metadata(code), ensure_ascii=False, indent=2)
    parts = [f"TARGET SUKL CODE: {code}\nMETADATA:\n{metadata}",
             f"DOCUMENT MANIFEST:\n{json.dumps(manifest)}", f"MISSING INPUTS: {json.dumps(missing)}",
             f"SEARCH COVERAGE:\n{json.dumps(corpus.status())}", "PRIMARY DOCUMENT TEXT:"]
    budget = settings.context_chars
    # Alternate documents so a long SPC never prevents the PIL from entering initial context.
    for number in range(max(len(p) for p in all_pages.values())):
        for doc in docs:
            if number >= len(all_pages[doc.id]):
                continue
            page = all_pages[doc.id][number]
            take = min(budget, len(page["text"]))
            if take or not page["text"]:
                parts.append(f"\n[{doc.id} PAGE {page['page']} CHARS 0:{take}/{len(page['text'])}]\n"
                             + page["text"][:take])
                deps.expose(doc.id, page["page"], 0, take)
                budget -= take
    parts.append("UNREAD RANGES (fetch these before final output):\n" + json.dumps(deps.unread()))
    return "\n\n".join(parts), deps


def validate_evidence(deps: Dependencies, output: DrugExtraction) -> None:
    """Mechanical checks only: a real quote is not proof of semantic entailment."""
    unread = deps.unread()
    if unread:
        raise ValueError(f"Read all primary pages with read_pages first. Unread ranges: {unread[:12]}")

    def check(citation: Citation, primary: bool = False, ctcae: bool = False) -> str:
        doc_id = citation.document_id
        if doc_id not in deps.touched:
            raise ValueError(f"Citation references an unread document: {doc_id}")
        if primary and doc_id not in deps.primary_ids:
            raise ValueError(f"Drug claims/actions must cite target PIL/SPC, not {doc_id}")
        if ctcae and doc_id != "CTCAE:ctcae-v6.pdf":
            raise ValueError("CTCAE mappings must cite the CTCAE reference")
        text = deps.corpus.page(doc_id, citation.page)["text"]
        quote = normalize_quote(citation.quote)
        if quote not in normalize_quote(text):
            raise ValueError(f"Quote not found verbatim in {doc_id} page {citation.page}: {quote[:90]}")
        return text

    def primary_support(citations: list[Citation]) -> None:
        if not any(c.document_id in deps.primary_ids for c in citations):
            raise ValueError("Every drug claim requires at least one target PIL/SPC citation")
        for citation in citations:
            check(citation)

    effects = list(output.side_effects)
    for combination in output.combinations:
        primary_support(combination.evidence)
        effects.extend(combination.side_effects)
    for effect in effects:
        primary_support(effect.evidence)
        for citation in effect.action_evidence:
            check(citation, primary=True)
        if effect.ctcae is not None:
            found_heading = False
            for citation in effect.ctcae_evidence:
                text = check(citation, ctcae=True)
                # CTCAE v6 has a term heading immediately before each Grade 1 ... row.
                headings = []
                lines = text.splitlines()
                for i, line in enumerate(lines):
                    if "Grade 1" in line and "Grade 2" in line:
                        j = i - 1
                        while j >= 0 and not lines[j].strip():
                            j -= 1
                        if j >= 0:
                            headings.append(normalize_quote(lines[j]))
                found_heading |= normalize_quote(effect.ctcae) in headings
            if not found_heading:
                raise ValueError(f"Not an exact CTCAE term heading on cited pages: {effect.ctcae}")
        else:
            for citation in effect.ctcae_evidence:
                check(citation, ctcae=True)


def build_agent(model: Model, vision: bool = False) -> Agent[Dependencies, DrugExtraction]:
    agent = Agent(model, deps_type=Dependencies, output_type=DrugExtraction,
                  instructions=INSTRUCTIONS, retries=2, name="sukl_extractor")

    @agent.tool
    async def find_drugs(ctx: RunContext[Dependencies], query: str) -> dict:
        """Find drugs by brand, active substance (Czech/English/INN), SUKL code or ATC.

        Searches all CSV metadata. Returns document IDs usable in read_pages, whether
        or not the PDFs have been indexed. Use a short name/substance query.
        """
        return {"matches": ctx.deps.corpus.catalog.find_drugs(query)}

    @agent.tool
    async def search_documents(ctx: RunContext[Dependencies], query: str,
                               kind: str | None = None, document_id: str | None = None) -> dict:
        """Search indexed text using AND keywords, without query syntax.

        kind can be PIL, SPC or CTCAE. Use English for CTCAE, Czech for SUKL PDFs.
        With document_id, indexes that PDF on demand. Use fewer words if no results.
        Global coverage is reported; an empty hit list is not evidence of absence.
        """
        try:
            hits = ctx.deps.corpus.search(query, kind=kind, document_id=document_id)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        ctx.deps.touched.update(hit["document_id"] for hit in hits)
        return {"hits": hits, "coverage": ctx.deps.corpus.status()}

    @agent.tool
    async def read_pages(ctx: RunContext[Dependencies], document_id: str, page: int = 1,
                         count: int = 2, start_char: int = 0) -> dict:
        """Read up to 4 consecutive PDF pages (24,000 characters per call).

        Page numbers are 1-based. start_char applies to the first requested page.
        Follow next_start_char to finish a truncated page; page_count gives coverage.
        Any catalog document is available, including PDFs absent from the search index.
        """
        if page < 1 or not 1 <= count <= 4 or start_char < 0:
            raise ModelRetry("Require page>=1, 1<=count<=4, start_char>=0")
        try:
            pages = ctx.deps.corpus.pages(document_id)
            if page > len(pages):
                raise ValueError(f"Only {len(pages)} pages in {document_id}")
            result, budget = [], 24_000
            for row in pages[page - 1:page - 1 + count]:
                start = start_char if row["page"] == page else 0
                if start > len(row["text"]):
                    raise ValueError("start_char exceeds page length")
                end = min(len(row["text"]), start + budget)
                ctx.deps.expose(document_id, row["page"], start, end)
                result.append({"document_id": document_id, "page": row["page"],
                               "text": row["text"][start:end], "start_char": start,
                               "next_start_char": end if end < len(row["text"]) else None})
                budget -= end - start
                if not budget:
                    break
            return {"pages": result, "page_count": len(pages), "unread_primary": ctx.deps.unread()}
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc

    if vision:
        @agent.tool
        async def page_image(ctx: RunContext[Dependencies], document_id: str, page: int) -> BinaryContent:
            """Render a PDF page as an image to inspect ambiguous table layout (max 4 per run).

            Read the text too, for verifiable citations. Requires a vision-capable deployment.
            """
            if ctx.deps.image_count >= 4:
                raise ModelRetry("Image budget exhausted; use read_pages")
            try:
                ctx.deps.corpus.page(document_id, page)
                data = ctx.deps.corpus.catalog.documents[document_id].render(page)
            except ValueError as exc:
                raise ModelRetry(str(exc)) from exc
            ctx.deps.touched.add(document_id)
            ctx.deps.image_count += 1
            return BinaryContent(data=data, media_type="image/png")

    @agent.output_validator
    async def grounded(ctx: RunContext[Dependencies], output: DrugExtraction) -> DrugExtraction:
        try:
            validate_evidence(ctx.deps, output)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        for missing in ctx.deps.missing:
            note = f"Missing primary document: {missing}"
            if note not in output.uncertainties:
                output.uncertainties.append(note)
        return output

    return agent


def fingerprint(deps: Dependencies, settings: Settings) -> str:
    # Cache warming by this run must not invalidate resume. Source inventory, prompt,
    # schema and configuration changes do invalidate it, including contextual sources.
    corpus = deps.corpus
    payload = {"metadata": corpus.catalog.metadata(deps.code), "csv_hashes": corpus.catalog.csv_hashes,
               "missing": deps.missing,
               "instructions": INSTRUCTIONS, "prompt_version": PROMPT_VERSION,
               "schema": DrugExtraction.model_json_schema(), "settings": asdict(settings),
               "corpus_sources": sorted((d.id, d.signature) for d in corpus.catalog.documents.values())}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", delete=False) as f:
        temp = Path(f.name)
        try:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


async def extract(prompt: str, deps: Dependencies, settings: Settings, *,
                  model: Model | None = None, trace: bool = False) -> dict:
    # Inject a FunctionModel in tests; no real client/credentials are then constructed.
    client = None
    if model is None:
        key = os.environ.get("API_KEY")
        if not key:
            raise ValueError("Set API_KEY before calling the Azure gateway")
        client = AsyncOpenAI(
            api_key=key, base_url=settings.base_url,
            default_headers={"api-key": os.environ["API_KEY"]}, timeout=180.0, max_retries=3,
        )
        model = OpenAIChatModel(settings.model, provider=OpenAIProvider(openai_client=client))
    run_fingerprint = fingerprint(deps, settings)
    try:
        result = await build_agent(model, settings.vision).run(
            prompt, deps=deps,
            model_settings={"max_tokens": settings.max_output_tokens, **settings.model_settings},
            usage_limits=UsageLimits(request_limit=settings.request_limit,
                                     tool_calls_limit=settings.tool_limit,
                                     total_tokens_limit=settings.total_tokens),
        )
    finally:
        if client is not None:
            await client.close()
    sources = [r for r in deps.corpus.provenance() if r["id"] in deps.touched]
    artifact = {
        "schema_version": "1", "prompt_version": PROMPT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(), "fingerprint": run_fingerprint,
        "sukl_code": deps.code, "metadata": deps.corpus.catalog.metadata(deps.code),
        "csv_hashes": deps.corpus.catalog.csv_hashes,
        "model": settings.model, "settings": asdict(settings),
        "schema_sha256": hashlib.sha256(json.dumps(DrugExtraction.model_json_schema(), sort_keys=True).encode()).hexdigest(),
        "prompt_sha256": hashlib.sha256((INSTRUCTIONS + prompt).encode()).hexdigest(),
        "usage": TypeAdapter(type(result.usage)).dump_python(result.usage, mode="json"),
        "sources": sources,
        "search_coverage": deps.corpus.status(),
        "review_status": "unreviewed", "extraction": result.output.model_dump(mode="json"),
    }
    if trace:
        artifact["messages"] = json.loads(result.all_messages_json())
    return artifact
