from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from pydantic_ai.exceptions import UsageLimitExceeded

from .corpus import Catalog, Corpus, KINDS
from .extraction import (
    DEFAULT_BASE_URL, DEFAULT_MODEL, Settings, atomic_json, extract, prepare,
)
from .schema import DrugExtraction


def positive(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Extract source-grounded drug data, one SUKL code at a time.")
    p.add_argument("--data", type=Path, default=Path("data"))
    p.add_argument("--snapshot", help="YYYYMMDD; auto-selected only with one extracted DLP snapshot")
    p.add_argument("--ctcae", type=Path, default=Path("ctcae-v6.pdf"))
    p.add_argument("--ctcae-csv", type=Path, default=Path("CTCAEv6.csv"),
                   help="CTCAE vocabulary for the prompt and soft validation")
    p.add_argument("--cache", type=Path, default=Path(".cache/sukl/corpus.sqlite"))
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Report catalog and index coverage; no LLM calls")
    find = commands.add_parser("find", help="Search CSV metadata; no LLM calls")
    find.add_argument("query")
    index = commands.add_parser("index", help="Cache PDF text for global search; resumable, no LLM calls")
    index.add_argument("--kind", choices=KINDS, action="append")
    index.add_argument("--limit", type=positive, help="Maximum NEW documents to extract this invocation")
    search = commands.add_parser("search", help="Search already indexed PDF text; no LLM calls")
    search.add_argument("query")
    search.add_argument("--kind", choices=KINDS)
    search.add_argument("--document", help="Optional document ID; loads it on demand")
    commands.add_parser("schema", help="Print the evolving output JSON schema; no data needed")
    for command in ("prepare", "extract"):
        sub = commands.add_parser(command, help="Prepare context offline" if command == "prepare" else "Run the LLM")
        sub.add_argument("code", nargs="?" if command == "extract" else None,
                         help="SUKL code, e.g. 0000009 (leading zeros optional)")
        sub.add_argument("--allow-partial", action="store_true", help="Allow a missing PIL or SPC; print a warning")
        sub.add_argument("--context-chars", type=positive, default=160_000)
        if command == "prepare":
            sub.add_argument("--output", type=Path, help="Prepared prompt file")
        if command == "extract":
            sub.add_argument("--ids-file", type=Path, help="SUKL codes separated by whitespace, commas or semicolons; # comments allowed")
            sub.add_argument("--parallelism", "--parallel", type=positive, default=1,
                             help="Maximum concurrent drug extractions (default: 1)")
            destination = sub.add_mutually_exclusive_group()
            destination.add_argument("--output", type=Path, help="Output file for a single code")
            destination.add_argument("--output-dir", type=Path, default=Path("outputs"),
                                     help="Per-drug output directory (default: outputs)")
            sub.add_argument("--model", default=os.environ.get("MODEL", DEFAULT_MODEL))
            sub.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", DEFAULT_BASE_URL))
            sub.add_argument("--api", choices=("responses", "chat"), default="responses",
                             help="API endpoint (default: responses); GPT-6 Luna tools on chat require reasoning effort none")
            sub.add_argument("--vision", action="store_true", help="Expose a bounded page-image tool")
            sub.add_argument("--model-settings", default="{}", help='JSON provider settings, e.g. {"openai_reasoning_effort":"low"}')
            sub.add_argument("--request-limit", type=positive, default=20,
                             help="Maximum model requests per drug (default: 20); last request produces final JSON")
            sub.add_argument("--tool-limit", type=positive,
                             help="Optional total tool-execution cap, including retries; exhaustion forces final JSON")
            sub.add_argument("--total-tokens", type=positive, default=400_000)
            sub.add_argument("--max-output-tokens", type=positive, default=24_000)
            overwrite = sub.add_mutually_exclusive_group()
            overwrite.add_argument("--resume", action="store_true", help="Skip an existing output if it matches the current schema")
            overwrite.add_argument("--force", action="store_true", help="Replace a previous output after a successful run")
            sub.add_argument("--trace", action="store_true",
                             help="Log tools and sizes live; save messages, per-request usage and errors to .trace.json")
    return p


def read_codes(path: Path, catalog: Catalog) -> list[str]:
    codes = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        for value in re.split(r"[\s,;]+", line.split("#", 1)[0].strip()):
            if not value:
                continue
            try:
                codes.append(catalog.code(value))
            except ValueError as exc:
                print(f"Warning: {path}:{number}: {exc}")
                # raise ValueError(f"{path}:{number}: {exc}") from exc
    if not codes:
        raise ValueError(f"No SUKL codes in {path}")
    return list(dict.fromkeys(codes))


async def extract_many(corpus: Corpus, codes: list[str], settings: Settings, args: argparse.Namespace) -> None:
    # A fixed number of workers keeps large input lists from creating thousands of tasks.
    # PDF/cache operations stay on this thread; only model calls overlap.
    pending = iter(codes)
    counts = {"written": 0, "reused": 0, "failed": 0}

    async def worker() -> None:
        for code in pending:
            output = args.output or args.output_dir / f"{code}.json"
            try:
                if output.exists() and not args.force:
                    if not args.resume:
                        raise ValueError(f"Output exists: {output}. Use --force to replace it.")
                    try:
                        DrugExtraction.model_validate_json(output.read_text(encoding="utf-8"))
                    except ValueError as exc:
                        raise ValueError(f"Existing output uses a different schema: {output}. Use --force to replace it.") from exc
                    counts["reused"] += 1
                    print(f"[{code}] Reusing {output}", flush=True)
                    continue
                print(f"[{code}] Preparing: {corpus.catalog.products[code]['NAZEV']}", file=sys.stderr, flush=True)
                prompt, deps = prepare(corpus, code, settings, args.allow_partial)
                if deps.missing:
                    print(f"[{code}] Warning: missing primary documents: {deps.missing}", file=sys.stderr)
                trace = output.with_suffix(".trace.json") if args.trace else None
                result = await extract(prompt, deps, settings, trace=trace)
                atomic_json(output, result)
                counts["written"] += 1
                print(f"[{code}] Wrote {output}; {len(result['side_effects'])} side effects, "
                      f"{len(result['combinations'])} combinations", flush=True)
            except Exception as exc:
                if args.ids_file is None:
                    raise
                counts["failed"] += 1
                print(f"[{code}] Failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    await asyncio.gather(*(worker() for _ in range(min(args.parallelism, len(codes)))))
    if args.ids_file is not None:
        print(f"Batch complete: {counts['written']} written, {counts['reused']} reused, {counts['failed']} failed.")
    if counts["failed"]:
        raise SystemExit(1)


def run(args: argparse.Namespace) -> None:
    if args.command == "schema":
        print(json.dumps(DrugExtraction.model_json_schema(), indent=2, ensure_ascii=False))
        return
    catalog = Catalog(args.data, args.ctcae, args.snapshot)
    corpus = Corpus(catalog, args.cache)
    try:
        if args.command == "status":
            print(json.dumps({"snapshot": catalog.snapshot, "products": len(catalog.products),
                              **corpus.status()}, ensure_ascii=False, indent=2))
        elif args.command == "find":
            print(json.dumps(catalog.find_drugs(args.query), ensure_ascii=False, indent=2))
        elif args.command == "search":
            print(json.dumps({"hits": corpus.search(args.query, args.kind, args.document),
                              "coverage": corpus.status()}, ensure_ascii=False, indent=2))
        elif args.command == "index":
            new, cached, failed = 0, 0, []
            for doc in catalog.documents.values():
                if args.kind and doc.kind not in args.kind:
                    continue
                try:
                    if corpus.ensure(doc.id):
                        new += 1
                        print(f"Indexed {doc.id} ({new} new)", file=sys.stderr)
                    else:
                        cached += 1
                except (ValueError, subprocess.SubprocessError, OSError) as exc:
                    failed.append({"document_id": doc.id, "error": str(exc)})
                    print(f"Failed {doc.id}: {exc}", file=sys.stderr)
                if args.limit and new >= args.limit:
                    break
            print(json.dumps({"new": new, "cached": cached, "failed": failed,
                              **corpus.status()}, ensure_ascii=False, indent=2))
            if failed:
                raise SystemExit(1)
        else:
            settings = Settings(context_chars=args.context_chars, ctcae_csv=args.ctcae_csv)
            if args.command == "extract":
                if (args.code is None) == (args.ids_file is None):
                    raise ValueError("Provide either a SUKL code or --ids-file PATH, exclusively.")
                if args.ids_file is not None and args.output is not None:
                    raise ValueError("--output is for a single code. Use --output-dir with --ids-file.")
                codes = read_codes(args.ids_file, catalog) if args.ids_file else [catalog.code(args.code)]
                model_settings = json.loads(args.model_settings)
                if not isinstance(model_settings, dict):
                    raise ValueError("--model-settings must be a JSON object")
                settings = Settings(
                    model=args.model, base_url=args.base_url, api=args.api, context_chars=args.context_chars,
                    ctcae_csv=args.ctcae_csv,
                    request_limit=args.request_limit, tool_limit=args.tool_limit,
                    total_tokens=args.total_tokens, max_output_tokens=args.max_output_tokens,
                    vision=args.vision, model_settings=model_settings,
                )
                if not settings.model.strip():
                    raise ValueError("--model / MODEL must not be empty")
                asyncio.run(extract_many(corpus, codes, settings, args))
                return
            code = catalog.code(args.code)
            print(f"Preparing {code}: {catalog.products[code]['NAZEV']}", file=sys.stderr)
            prompt, deps = prepare(corpus, code, settings, args.allow_partial)
            if deps.missing:
                print(f"Warning: missing primary documents: {deps.missing}", file=sys.stderr)
            if args.command == "prepare":
                if args.output:
                    args.output.parent.mkdir(parents=True, exist_ok=True)
                    args.output.write_text(prompt, encoding="utf-8")
                    print(f"Prepared context: {args.output}", file=sys.stderr)
                else:
                    print(prompt)
                return
    finally:
        corpus.close()


def main() -> None:
    p = parser()
    try:
        run(p.parse_args())
    except (ValueError, OSError, subprocess.SubprocessError, UsageLimitExceeded) as exc:
        p.exit(1, f"Error: {exc}\n")
