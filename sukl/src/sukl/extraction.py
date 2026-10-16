from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Literal

from openai import AsyncOpenAI
from pydantic import TypeAdapter
from pydantic_ai import Agent, BinaryContent, ModelRetry, NativeOutput, RunContext, UsageLimits, capture_run_messages
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, ModelMessagesTypeAdapter, ModelResponse, RetryPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import RunUsage

from .corpus import Corpus
from .ctcae import load_terms, validation_warnings
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
    ctcae_csv: Path = Path("CTCAEv6.csv")
    request_limit: int = 20
    tool_limit: int | None = None
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
    tool_limit: int | None = None
    tool_calls: int = 0
    ctcae_terms: set[str] = field(default_factory=set)
    validation_feedback: list[str] = field(default_factory=list)
    validation_warnings: list[str] = field(default_factory=list)

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
    deps.ctcae_terms = load_terms(settings.ctcae_csv)
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
    parts.append(f"CTCAE V6 TERMS ({len(deps.ctcae_terms)} exact English terms; use these as ctcae values):\n"
                 + "\n".join(sorted(deps.ctcae_terms)))
    return "\n\n".join(parts), deps


def validate_coverage(deps: Dependencies) -> None:
    """Avoid silently omitting target text when it exceeds the initial context budget."""
    unread = deps.unread()
    if unread:
        raise ValueError(f"Read all primary pages with read_pages first. Unread ranges: {unread[:12]}")


def build_agent(model: Model, vision: bool = False) -> Agent[Dependencies, DrugExtraction]:
    hooks = Hooks()

    def tools_available(ctx: RunContext[Dependencies]) -> bool:
        request_limit = ctx.usage_limits.request_limit
        return (not ctx.deps.validation_feedback
                and (request_limit is None or ctx.usage.requests < request_limit - 1)
                and (ctx.deps.tool_limit is None or ctx.deps.tool_calls < ctx.deps.tool_limit))

    @hooks.on.prepare_tools
    async def prepare_tools(ctx, tool_defs):
        return tool_defs if tools_available(ctx) else []

    @hooks.on.tool_execute
    async def limit_tools(ctx, *, call, tool_def, args, handler):
        # Reserve a slot before awaiting: parallel batches can never exceed the cap.
        if ctx.deps.tool_limit is not None and ctx.deps.tool_calls >= ctx.deps.tool_limit:
            return {"tool_budget_exhausted": True,
                    "message": "No further tools are possible. Return the final JSON from available evidence now."}
        ctx.deps.tool_calls += 1
        return await handler(args)

    agent = Agent(model, deps_type=Dependencies, output_type=NativeOutput(DrugExtraction, strict=True),
                  instructions=INSTRUCTIONS, retries=2, name="sukl_extractor", capabilities=[hooks])

    @agent.instructions
    async def budget(ctx: RunContext[Dependencies]) -> str:
        if ctx.deps.validation_feedback:
            return ("No further tools are possible. Review the soft validation warnings and return the "
                    "complete final JSON now. This is the only correction pass; remaining warnings are "
                    "allowed. This overrides instructions to fetch more pages or search CTCAE.")
        if not tools_available(ctx):
            return ("No further tools are possible. You MUST return the final JSON now using only "
                    "evidence already supplied. Use null for unverified CTCAE terms; do not invent "
                    "missing information. This overrides instructions to fetch more pages or search CTCAE.")
        remaining = ctx.usage_limits.request_limit - ctx.usage.requests
        tool_budget = ("" if ctx.deps.tool_limit is None else
                       f" At most {ctx.deps.tool_limit - ctx.deps.tool_calls} tool executions remain.")
        return (f"You have {remaining} model requests left, including this one. The last request is "
                "reserved for final JSON with tools disabled. Read unread primary pages before optional searches."
                + tool_budget)

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
        if tools_available(ctx):
            try:
                validate_coverage(ctx.deps)
            except ValueError as exc:
                raise ModelRetry(str(exc)) from exc
        warnings = validation_warnings(output, ctx.deps.ctcae_terms)
        ctx.deps.validation_warnings = warnings
        request_limit = ctx.usage_limits.request_limit
        if warnings and not ctx.deps.validation_feedback and (request_limit is None or ctx.usage.requests < request_limit):
            ctx.deps.validation_feedback = warnings
            print(f"[{ctx.deps.code}] Warning: soft validation found {len(warnings)} unknown CTCAE term(s); requesting one correction pass.",
                  file=sys.stderr, flush=True)
            raise ModelRetry("Soft validation warnings:\n" + "\n".join(warnings)
                             + "\nReview these mappings using the supplied CTCAE list and source text. "
                             "Prefer an exact listed term or null if no suitable mapping exists. "
                             "Return the complete JSON again, not a patch. This is the only correction pass; "
                             "the second answer may keep warnings if you judge the values appropriate. "
                             "Do not call tools.")
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
    # Tool exhaustion is handled by the hooks so the model can still produce final JSON.
    limits = UsageLimits(request_limit=settings.request_limit, total_tokens_limit=settings.total_tokens)
    deps.tool_limit, deps.tool_calls = settings.tool_limit, 0
    deps.validation_feedback, deps.validation_warnings = [], []
    usage = RunUsage()
    request_start = RunUsage()
    tool_calls = []
    trace_prefix = f"[trace {deps.code}]"
    error = None
    agent = build_agent(model, settings.vision)
    with capture_run_messages() as messages:
        try:
            async with agent.iter(prompt, deps=deps, model_settings=model_settings,
                                  usage_limits=limits, usage=usage) as run:
                async for node in run:
                    if trace is None:
                        continue
                    if Agent.is_model_request_node(node):
                        request_start = deepcopy(usage)
                        print(f"{trace_prefix} Request {usage.requests + 1}/{settings.request_limit}; "
                              f"tools executed={deps.tool_calls}",
                              file=sys.stderr, flush=True)
                    elif Agent.is_call_tools_node(node):
                        request_usage = node.model_response.usage
                        print(f"{trace_prefix} Tokens: input={request_usage.input_tokens:,} "
                              f"output={request_usage.output_tokens:,}; cumulative={usage.total_tokens:,}",
                              file=sys.stderr, flush=True)
                        # Stream tool events only; model requests retain the usual non-streaming API.
                        async with node.stream(run.ctx) as events:
                            async for event in events:
                                if isinstance(event, FunctionToolCallEvent):
                                    part = event.part
                                    arguments = part.args_as_json_str()
                                    call = {"request": usage.requests, "tool_call_id": part.tool_call_id,
                                            "tool": part.tool_name, "arguments": part.args,
                                            "input_chars": len(arguments), "status": "started"}
                                    tool_calls.append(call)
                                    print(f"{trace_prefix} Tool {len(tool_calls)}: {part.tool_name}({arguments}) "
                                          f"input={len(arguments):,} chars", file=sys.stderr, flush=True)
                                elif isinstance(event, FunctionToolResultEvent):
                                    call = next(c for c in reversed(tool_calls)
                                                if c["tool_call_id"] == event.tool_call_id)
                                    part = event.part
                                    retry = isinstance(part, RetryPromptPart)
                                    text = part.model_response() if retry else part.model_response_str()
                                    binary_bytes = 0 if retry else sum(
                                        len(f.data) for f in part.files if isinstance(f, BinaryContent))
                                    blocked = (not retry and isinstance(part.content, dict)
                                               and part.content.get("tool_budget_exhausted") is True)
                                    call.update(status="retry" if retry else "blocked" if blocked else "ok", output_chars=len(text),
                                                output_binary_bytes=binary_bytes)
                                    print(f"{trace_prefix} {call['tool']}: {call['status']}; "
                                          f"output={len(text):,} chars, binary={binary_bytes:,} bytes",
                                          file=sys.stderr, flush=True)
                result = run.result
                assert result is not None
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            try:
                if client is not None:
                    await client.close()
            finally:
                if trace is not None:
                    requests = [{"request": i, "usage": TypeAdapter(type(m.usage)).dump_python(m.usage, mode="json")}
                                for i, m in enumerate((m for m in messages if isinstance(m, ModelResponse)), 1)]
                    # Token-limit errors can be raised after usage is recorded but before
                    # Pydantic AI appends the response to message history.
                    last_usage = usage - request_start
                    if last_usage.total_tokens and len(requests) == request_start.requests:
                        requests.append({"request": request_start.requests + 1, "response_saved": False,
                                         "usage": TypeAdapter(RunUsage).dump_python(last_usage, mode="json")})
                    atomic_json(trace, {
                        "code": deps.code, "model": settings.model, "status": "failed" if error else "completed", "error": error,
                        "limits": {"requests": settings.request_limit, "tool_calls": settings.tool_limit,
                                   "total_tokens": settings.total_tokens},
                        "usage": TypeAdapter(RunUsage).dump_python(usage, mode="json"),
                        "tool_executions": deps.tool_calls, "unread_primary": deps.unread(),
                        "validation_feedback": deps.validation_feedback,
                        "validation_warnings": deps.validation_warnings,
                        "requests": requests, "tool_calls": tool_calls,
                        "messages": ModelMessagesTypeAdapter.dump_python(messages, mode="json"),
                    })
                    print(f"{trace_prefix} {'Failed' if error else 'Completed'}; "
                          f"tokens: input={usage.input_tokens:,} output={usage.output_tokens:,}; saved {trace}",
                          file=sys.stderr, flush=True)
    if deps.unread():
        print(f"[{deps.code}] Warning: budget exhausted before all primary text was read; result uses supplied evidence only. "
              "Increase --request-limit or --context-chars for full coverage.", file=sys.stderr)
    for warning in deps.validation_warnings:
        print(f"[{deps.code}] Warning: {warning}", file=sys.stderr)
    return result.output.model_dump(mode="json")
