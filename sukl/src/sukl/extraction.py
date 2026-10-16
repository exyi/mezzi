from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Literal

from openai import AsyncOpenAI
from pydantic import TypeAdapter
from pydantic_ai import Agent, BinaryContent, ModelRetry, NativeOutput, RunContext, UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from .corpus import Corpus
from .prompts import INSTRUCTIONS
from .schema import DrugExtraction

DEFAULT_BASE_URL = "https://apim-rakathon-q7146n.azure-api.net/openai/v1/"
DEFAULT_MODEL = "gpt-6-luna"


@dataclass
class Settings:
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api: Literal["responses", "chat"] = "responses"
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
    image_count: int = 0

    def expose(self, document_id: str, page: int, start: int, end: int) -> None:
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


def validate_coverage(deps: Dependencies) -> None:
    """Avoid silently omitting target text when it exceeds the initial context budget."""
    unread = deps.unread()
    if unread:
        raise ValueError(f"Read all primary pages with read_pages first. Unread ranges: {unread[:12]}")


def build_agent(model: Model, vision: bool = False) -> Agent[Dependencies, DrugExtraction]:
    agent = Agent(model, deps_type=Dependencies, output_type=NativeOutput(DrugExtraction, strict=True),
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

            Read the text too. Requires a vision-capable deployment.
            """
            if ctx.deps.image_count >= 4:
                raise ModelRetry("Image budget exhausted; use read_pages")
            try:
                ctx.deps.corpus.page(document_id, page)
                data = ctx.deps.corpus.catalog.documents[document_id].render(page)
            except ValueError as exc:
                raise ModelRetry(str(exc)) from exc
            ctx.deps.image_count += 1
            return BinaryContent(data=data, media_type="image/png")

    @agent.output_validator
    async def complete(ctx: RunContext[Dependencies], output: DrugExtraction) -> DrugExtraction:
        try:
            validate_coverage(ctx.deps)
        except ValueError as exc:
            raise ModelRetry(str(exc)) from exc
        return output

    return agent


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
                  model: Model | None = None, trace: Path | None = None) -> dict:
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
        model_class = OpenAIResponsesModel if settings.api == "responses" else OpenAIChatModel
        model = model_class(settings.model, provider=OpenAIProvider(openai_client=client))
    model_settings = {"max_tokens": settings.max_output_tokens, **settings.model_settings}
    if settings.api == "responses":
        # Replay history locally, including encrypted reasoning, without server-side storage.
        model_settings.setdefault("openai_store", False)
    try:
        result = await build_agent(model, settings.vision).run(
            prompt, deps=deps,
            model_settings=model_settings,
            usage_limits=UsageLimits(request_limit=settings.request_limit,
                                     tool_calls_limit=settings.tool_limit,
                                     total_tokens_limit=settings.total_tokens),
        )
    finally:
        if client is not None:
            await client.close()
    if trace is not None:
        atomic_json(trace, {
            "model": settings.model,
            "usage": TypeAdapter(type(result.usage)).dump_python(result.usage, mode="json"),
            "messages": json.loads(result.all_messages_json()),
        })
    return result.output.model_dump(mode="json")
