"""Offline integration tests; no credentials or paid models are used."""

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pydantic import TypeAdapter, ValidationError
import httpx2
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelResponse, RetryPromptPart, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai import BinaryContent
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.function import FunctionModel

from sukl.corpus import Catalog, Corpus
from sukl.extraction import Settings, atomic_json, extract, prepare, validate_coverage
from sukl.schema import ActionCondition, DrugExtraction, EffectType, Frequency, SideEffect


def pdf(pages: list[list[str]]) -> bytes:
    """Tiny real PDF fixture, built without a PDF library."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        page_number, stream_number = len(objects) + 1, len(objects) + 2
        kids.append(f"{page_number} 0 R")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_number} 0 R >>".encode())
        text = "BT /F1 12 Tf 50 730 Td "
        for i, line in enumerate(lines):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            text += ("0 -20 Td " if i else "") + f"({escaped}) Tj "
        stream = (text + "ET").encode("ascii")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(kids)}] >>".encode()
    result, offsets = b"%PDF-1.4\n", [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(result)
    result += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    result += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return result


def write_csv(path: Path, fields: list[str], rows: list[list[str]]) -> None:
    with path.open("w", encoding="cp1250", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(fields)
        writer.writerows(rows)


@unittest.skipUnless(shutil.which("pdftotext"), "Poppler pdftotext required")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        data = self.root / "data"
        dlp = data / "DLP20260925"
        dlp.mkdir(parents=True)
        write_csv(dlp / "dlp_lecivepripravky.csv", ["KOD_SUKL", "NAZEV", "SILA", "ATC_WHO"],
                  [["0000009", "LÉČIVO", "10MG", "A01"], ["0000010", "OTHER", "20MG", "A02"]])
        write_csv(dlp / "dlp_nazvydokumentu.csv", ["KOD_SUKL", "PIL", "SPC"],
                  [["0000009", "PI1.pdf", "SPC1.pdf"], ["0000010", "PI1.pdf", "missing.doc"]])
        write_csv(dlp / "dlp_latky.csv", ["KOD_LATKY", "NAZEV", "NAZEV_EN", "NAZEV_INN"],
                  [["12", "LÁTKA", "ACETYLSALICYLIC ACID", "ACIDUM ACETYLSALICYLICUM"]])
        write_csv(dlp / "dlp_slozeni.csv", ["KOD_SUKL", "KOD_LATKY", "S", "AMNT", "UN"],
                  [["0000009", "12", "L", "10", "MG"]])
        pil = data / "PIL20260925"
        spc = data / "SPC20260925"
        pil.mkdir()
        spc.mkdir()
        (pil / "PI1.pdf").write_bytes(pdf([["Nausea occurs commonly.", "Contact your doctor if severe."], ["Do not combine with drug X."]]))
        (pil / "legacy.doc").write_text("not a pdf")
        (spc / "SPC1.pdf").write_bytes(pdf([["Nausea occurs commonly.", "Drug X increases nausea."]]))
        self.ctcae = self.root / "ctcae.pdf"
        self.ctcae.write_bytes(pdf([["Nausea", "Grade 1        Grade 2        Grade 3", "Definition: A feeling of sickness."]]))
        self.ctcae_csv = self.root / "CTCAEv6.csv"
        self.ctcae_csv.write_text('CTCAE v6.0 MedDRA 28.0 Term\nNausea\nHeadache\n"Other, specify"\n', encoding="utf-8")
        self.catalog = Catalog(data, self.ctcae)
        self.corpus = Corpus(self.catalog, self.root / "cache.sqlite")
        self.settings = Settings(ctcae_csv=self.ctcae_csv)
        self.prompt, self.deps = prepare(self.corpus, "9", self.settings)

    def tearDown(self):
        self.corpus.close()
        self.temp.cleanup()

    def claim(self) -> dict:
        effect = {"ctcae": "Nausea", "frequency": "common",
                  "label": "Nauzea; kontaktujte lékaře při závažných příznacích",
                  "action": "contact_doctor", "action_condition": {"patient_guide": "Pokud je vám výrazně nevolno"}}
        return {"side_effects": [effect], "combinations": [{"drug": "X", "effect": "Zvyšuje riziko nauzey.",
                                                           "effect_type": "increased_toxicity", "side_effects": [effect]}]}

    def test_pdf_loading_encoding_and_exact_identity(self):
        self.assertEqual(self.catalog.metadata("9")["product"]["NAZEV"], "LÉČIVO")
        self.assertEqual(self.catalog.code("9"), "0000009")
        self.assertIn("Nausea occurs commonly.", self.prompt)
        self.assertIn("CTCAE V6 TERMS (3 exact English terms", self.prompt)
        self.assertIn("Other, specify", self.prompt)
        self.assertEqual(self.deps.ctcae_terms, {"Nausea", "Headache", "Other, specify"})
        self.assertEqual(len(self.corpus.pages("PIL:PI1.pdf")), 2)
        self.assertEqual(len(self.catalog.documents), 3)
        self.assertEqual(self.catalog.references["PIL:PI1.pdf"], ["0000009", "0000010"])
        self.assertEqual(len(self.catalog.unsupported), 1)
        self.assertFalse(self.corpus.ensure("PIL:PI1.pdf"))
        self.assertEqual(self.catalog.find_drugs("ACETYLSALICYLIC")[0]["code"], "0000009")
        self.assertEqual(self.catalog.find_drugs("lecivo")[0]["code"], "0000009")
        with self.assertRaises(ValueError):
            self.catalog.code("LÉČIVO")

    def test_fts_is_bounded_and_query_syntax_is_not_executed(self):
        hits = self.corpus.search("nausea", kind="SPC")
        self.assertEqual(hits[0]["document_id"], "SPC:SPC1.pdf")
        self.assertEqual(self.corpus.search('nausea OR "missing"'), [])
        self.assertEqual(self.corpus.search('"; --'), [])
        self.assertEqual(self.corpus.status()["indexed"]["CTCAE"], 1)

    def test_missing_inputs_fail_closed_or_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "Missing target"):
            prepare(self.corpus, "10", self.settings)
        _, deps = prepare(self.corpus, "10", self.settings, allow_partial=True)
        self.assertEqual(deps.missing, ["SPC: missing.doc"])

    def test_page_coverage_detects_gaps_and_truncation(self):
        _, deps = prepare(self.corpus, "9", Settings(context_chars=8, ctcae_csv=self.ctcae_csv))
        with self.assertRaisesRegex(ValueError, "Read all primary"):
            validate_coverage(deps)
        for doc_id in deps.primary_ids:
            for row in self.corpus.pages(doc_id):
                deps.expose(doc_id, row["page"], 0, len(row["text"]))
        self.assertEqual(deps.unread(), [])
        deps.exposed[("PIL:PI1.pdf", 1)] = [(0, 4), (8, 1000)]
        self.assertEqual(deps.unread()[0]["start_char"], 4)

    def test_changed_source_invalidates_text_cache(self):
        self.ctcae.write_bytes(pdf([["Headache", "Grade 1        Grade 2        Grade 3"]]))
        self.assertTrue(self.corpus.ensure("CTCAE:ctcae-v6.pdf"))
        self.assertEqual(self.corpus.search("Nausea", kind="CTCAE"), [])
        self.assertTrue(self.corpus.search("Headache", kind="CTCAE"))

    def test_native_json_tools_schema_repair_and_separate_trace(self):
        calls = 0
        good = self.claim()

        async def model(messages, info):
            nonlocal calls
            calls += 1
            self.assertFalse(info.output_tools)
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("search_documents", {"query": "Nausea", "kind": "CTCAE"})])
            if calls == 2:
                self.assertTrue(any(isinstance(p, ToolReturnPart) for p in messages[-1].parts))
                bad = deepcopy(good)
                bad["side_effects"][0]["action"] = "invented_action"
                return ModelResponse(parts=[TextPart(json.dumps(bad))])
            self.assertTrue(any(isinstance(p, RetryPromptPart) for p in messages[-1].parts))
            return ModelResponse(parts=[TextPart(json.dumps(good))])

        trace = self.root / "result.trace.json"
        console = io.StringIO()
        with redirect_stderr(console):
            result = asyncio.run(extract(self.prompt, self.deps, self.settings, model=FunctionModel(model), trace=trace))
        self.assertEqual(calls, 3)
        self.assertEqual(result, good)
        self.assertEqual(set(result), {"side_effects", "combinations"})
        self.assertEqual(set(result["side_effects"][0]), {"ctcae", "frequency", "label", "action", "action_condition"})
        path = self.root / "result.json"
        atomic_json(path, result)
        self.assertEqual(json.loads(path.read_text()), good)
        debug = json.loads(trace.read_text())
        self.assertEqual(debug["usage"]["requests"], 3)
        self.assertEqual(debug["status"], "completed")
        self.assertIsNone(debug["error"])
        self.assertEqual(len(debug["requests"]), 3)
        call, = debug["tool_calls"]
        self.assertEqual(call["tool"], "search_documents")
        self.assertEqual(call["arguments"], {"query": "Nausea", "kind": "CTCAE"})
        self.assertEqual(call["request"], 1)
        self.assertEqual(call["status"], "ok")
        self.assertGreater(call["input_chars"], 0)
        self.assertGreater(call["output_chars"], 0)
        self.assertIn("search_documents", console.getvalue())
        self.assertIn("output=", console.getvalue())
        self.assertIn("messages", debug)

    def test_valid_output_needs_one_request_and_no_tools(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            self.assertIn("No CTCAE tool calls", info.instructions)
            self.assertIn("CTCAE V6 TERMS", messages[0].parts[0].content)
            return ModelResponse(parts=[TextPart(json.dumps(self.claim()))])

        result = asyncio.run(extract(self.prompt, self.deps, self.settings, model=FunctionModel(model)))
        self.assertEqual(calls, 1)
        self.assertEqual(self.deps.tool_calls, 0)
        self.assertEqual(result, self.claim())

    def test_soft_validation_warns_once_and_allows_remaining_warnings(self):
        for correct in (True, False):
            with self.subTest(correct=correct):
                calls = 0
                bad = self.claim()
                bad["side_effects"][0]["ctcae"] = "Invented nausea"
                bad["combinations"][0]["side_effects"][0]["ctcae"] = "Not a CTCAE term"

                async def model(messages, info):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return ModelResponse(parts=[TextPart(json.dumps(bad))])
                    self.assertEqual(calls, 2)
                    self.assertEqual(info.function_tools, [])
                    self.assertIn("only correction pass", info.instructions)
                    feedback = "\n".join(p.content for p in messages[-1].parts if isinstance(p, RetryPromptPart))
                    self.assertIn("side_effects[0].ctcae", feedback)
                    self.assertIn("combinations[0].side_effects[0].ctcae", feedback)
                    self.assertIn("second answer may keep warnings", feedback)
                    return ModelResponse(parts=[TextPart(json.dumps(self.claim() if correct else bad))])

                trace = self.root / f"validation-{correct}.trace.json"
                console = io.StringIO()
                with redirect_stderr(console):
                    result = asyncio.run(extract(self.prompt, self.deps, Settings(request_limit=2),
                                                model=FunctionModel(model), trace=trace))
                self.assertEqual(calls, 2)
                self.assertEqual(result, self.claim() if correct else bad)
                debug = json.loads(trace.read_text())
                self.assertEqual(len(debug["validation_feedback"]), 2)
                self.assertEqual(len(debug["validation_warnings"]), 0 if correct else 2)
                self.assertEqual(debug["tool_executions"], 0)
                self.assertIn("requesting one correction pass", console.getvalue())
                if not correct:
                    self.assertIn("Warning: combinations[0].side_effects[0].ctcae", console.getvalue())

    def test_soft_validation_respects_exhausted_request_budget(self):
        calls = 0
        bad = self.claim()
        bad["side_effects"][0]["ctcae"] = "Invented nausea"

        async def model(messages, info):
            nonlocal calls
            calls += 1
            return ModelResponse(parts=[TextPart(json.dumps(bad))])

        console = io.StringIO()
        with redirect_stderr(console):
            result = asyncio.run(extract(self.prompt, self.deps, Settings(request_limit=1), model=FunctionModel(model)))
        self.assertEqual(calls, 1)
        self.assertEqual(result, bad)
        self.assertIn("is not an exact CTCAE", console.getvalue())
        self.assertEqual(self.deps.validation_feedback, [])

    def test_tool_limit_trims_parallel_batch_and_requests_final_json(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 3:
                self.assertEqual(info.function_tools, [])
                self.assertIn("No further tools are possible", info.instructions)
                returns = [p for p in messages[-1].parts if isinstance(p, ToolReturnPart)]
                self.assertEqual(sum(p.content.get("tool_budget_exhausted", False) for p in returns), 1)
                return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])
            return ModelResponse(parts=[ToolCallPart(
                "search_documents", {"query": "Nausea", "kind": "CTCAE"},
                tool_call_id=f"call-{calls}-{i}") for i in range(2)])

        # The second batch has two calls but only one slot; preserve the allowed result.
        for traced in (False, True):
            with self.subTest(traced=traced):
                calls = 0
                trace = self.root / "limited.trace.json" if traced else None
                with patch.object(self.corpus, "search", wraps=self.corpus.search) as search:
                    result = asyncio.run(extract(self.prompt, self.deps, Settings(tool_limit=3),
                                                model=FunctionModel(model), trace=trace))
                    self.assertEqual(search.call_count, 3)
                self.assertEqual(calls, 3)
                self.assertEqual(result, {"side_effects": [], "combinations": []})
                if traced:
                    debug = json.loads(trace.read_text())
                    self.assertEqual(debug["status"], "completed")
                    self.assertEqual(debug["limits"]["tool_calls"], 3)
                    self.assertEqual(debug["tool_executions"], 3)
                    self.assertEqual([c["status"] for c in debug["tool_calls"]], ["ok", "ok", "ok", "blocked"])

    def test_request_budget_allows_many_tools_then_disables_them_for_final_request(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertTrue(info.function_tools)
                self.assertIn("2 model requests left", info.instructions)
                return ModelResponse(parts=[ToolCallPart(
                    "search_documents", {"query": "Nausea", "kind": "CTCAE"},
                    tool_call_id=f"batch-{i}") for i in range(30)])
            self.assertEqual(info.function_tools, [])
            self.assertIn("MUST return the final JSON now", info.instructions)
            self.assertEqual(sum(isinstance(p, ToolReturnPart) for p in messages[-1].parts), 30)
            return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])

        with patch.object(self.corpus, "search", wraps=self.corpus.search) as search:
            result = asyncio.run(extract(self.prompt, self.deps, Settings(request_limit=2), model=FunctionModel(model)))
            self.assertEqual(search.call_count, 30)
        self.assertEqual(calls, 2)
        self.assertEqual(result, {"side_effects": [], "combinations": []})

    def test_final_request_returns_partial_coverage_with_warning(self):
        prompt, deps = prepare(self.corpus, "9", Settings(context_chars=8, ctcae_csv=self.ctcae_csv))

        async def model(messages, info):
            self.assertEqual(info.function_tools, [])
            self.assertIn("No further tools are possible", info.instructions)
            return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])

        console = io.StringIO()
        trace = self.root / "partial.trace.json"
        with redirect_stderr(console):
            asyncio.run(extract(prompt, deps, Settings(request_limit=1), model=FunctionModel(model), trace=trace))
        self.assertIn("before all primary text was read", console.getvalue())
        self.assertTrue(json.loads(trace.read_text())["unread_primary"])

    def test_token_limit_saves_usage_even_when_last_response_is_missing(self):
        async def model(messages, info):
            return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])

        trace = self.root / "tokens.trace.json"
        with self.assertRaises(UsageLimitExceeded):
            asyncio.run(extract(self.prompt, self.deps, Settings(total_tokens=1),
                                model=FunctionModel(model), trace=trace))
        debug = json.loads(trace.read_text())
        self.assertEqual(debug["status"], "failed")
        self.assertGreater(debug["usage"]["input_tokens"], 0)
        self.assertEqual(len(debug["requests"]), 1)
        self.assertFalse(debug["requests"][0]["response_saved"])
        self.assertEqual(debug["requests"][0]["usage"]["input_tokens"], debug["usage"]["input_tokens"])

    def test_trace_records_tool_retry_and_subsequent_success(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls <= 2:
                return ModelResponse(parts=[ToolCallPart(
                    "read_pages", {"document_id": "PIL:PI1.pdf", "page": 99 if calls == 1 else 1},
                    tool_call_id=f"read-{calls}")])
            return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])

        trace = self.root / "retry.trace.json"
        asyncio.run(extract(self.prompt, self.deps, Settings(tool_limit=2), model=FunctionModel(model), trace=trace))
        debug = json.loads(trace.read_text())
        self.assertEqual([c["status"] for c in debug["tool_calls"]], ["retry", "ok"])
        self.assertTrue(all(c["output_chars"] > 0 for c in debug["tool_calls"]))
        self.assertEqual(debug["usage"]["tool_calls"], 1)
        self.assertEqual(debug["tool_executions"], 2)

    def test_agent_reads_omitted_page_ranges(self):
        prompt, deps = prepare(self.corpus, "9", Settings(context_chars=8, ctcae_csv=self.ctcae_csv))

        async def model(messages, info):
            unread = deps.unread()
            if unread:
                return ModelResponse(parts=[ToolCallPart("read_pages", {**unread[0], "count": 1})])
            return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])

        result = asyncio.run(extract(prompt, deps, self.settings, model=FunctionModel(model)))
        self.assertEqual(deps.unread(), [])
        self.assertEqual(result, {"side_effects": [], "combinations": []})

    def test_cli_saves_plain_json_with_optional_trace_and_can_resume(self):
        from sukl.cli import parser, run

        calls = 0
        good = self.claim()
        output = self.root / "drug.json"

        async def model(messages, info):
            nonlocal calls
            calls += 1
            return ModelResponse(parts=[TextPart(json.dumps(good))])

        async def offline_extract(prompt, deps, settings, *, trace):
            return await extract(prompt, deps, settings, model=FunctionModel(model), trace=trace)

        args = ["--data", str(self.root / "data"), "--ctcae", str(self.ctcae),
                "--ctcae-csv", str(self.ctcae_csv),
                "--cache", str(self.root / "cli.sqlite"), "extract", "9", "--output", str(output), "--trace"]
        with patch("sukl.cli.extract", offline_extract):
            run(parser().parse_args(args))
            run(parser().parse_args([*args, "--resume"]))
        self.assertEqual(calls, 1)
        self.assertEqual(json.loads(output.read_text()), good)
        self.assertIn("messages", json.loads(output.with_suffix(".trace.json").read_text()))

    def batch_args(self, output_dir: Path) -> list[str]:
        # Five drugs sharing documents exercise cache reuse as well as concurrency.
        codes = [str(n).zfill(7) for n in range(9, 14)]
        dlp = self.root / "data" / "DLP20260925"
        write_csv(dlp / "dlp_lecivepripravky.csv", ["KOD_SUKL", "NAZEV", "SILA", "ATC_WHO"],
                  [[code, f"Drug {code}", "10MG", "A01"] for code in codes])
        write_csv(dlp / "dlp_nazvydokumentu.csv", ["KOD_SUKL", "PIL", "SPC"],
                  [[code, "PI1.pdf", "SPC1.pdf"] for code in codes])
        ids = self.root / "ids.txt"
        ids.write_text("\ufeff# drug list\n9,0000009 # duplicate after normalization\n10;11\n12\t13\n", encoding="utf-8")
        return ["--data", str(self.root / "data"), "--ctcae", str(self.ctcae),
                "--ctcae-csv", str(self.ctcae_csv), "--cache", str(self.root / "cli.sqlite"),
                "extract", "--ids-file", str(ids), "--output-dir", str(output_dir), "--trace"]

    def test_batch_bounds_concurrency_deduplicates_and_isolates_traces(self):
        from sukl.cli import parser, run

        for parallelism in (1, 2):
            with self.subTest(parallelism=parallelism):
                output_dir = self.root / f"batch-{parallelism}"
                args = self.batch_args(output_dir) + ["--parallelism", str(parallelism)]
                active = peak = 0
                completed = []

                async def offline_extract(prompt, deps, settings, *, trace):
                    async def model(messages, info):
                        nonlocal active, peak
                        active += 1
                        peak = max(peak, active)
                        # Yield while both runs have active message capture contexts.
                        await asyncio.sleep(0.01)
                        active -= 1
                        good = self.claim()
                        good["combinations"][0]["drug"] = deps.code
                        return ModelResponse(parts=[TextPart(json.dumps(good))])

                    result = await extract(prompt, deps, settings, model=FunctionModel(model), trace=trace)
                    completed.append(deps.code)
                    return result

                with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()), patch("sukl.cli.extract", offline_extract):
                    run(parser().parse_args(args))
                    # Resume should avoid all preparation, model calls and trace rewrites.
                    with patch("sukl.cli.prepare", side_effect=AssertionError("Resumed outputs must not be prepared")):
                        run(parser().parse_args([*args, "--resume"]))
                self.assertEqual(peak, parallelism)
                self.assertEqual(len(completed), 5)
                self.assertEqual(len(set(completed)), 5)
                for code in completed:
                    result = json.loads((output_dir / f"{code}.json").read_text())
                    self.assertEqual(result["combinations"][0]["drug"], code)
                    debug = json.loads((output_dir / f"{code}.trace.json").read_text())
                    self.assertEqual(debug["code"], code)
                    self.assertEqual(debug["usage"]["requests"], 1)
                    self.assertEqual(len(debug["requests"]), 1)
                    history = json.dumps(debug["messages"])
                    self.assertIn(f"TARGET SUKL CODE: {code}", history)
                    for other in set(completed) - {code}:
                        self.assertNotIn(f"TARGET SUKL CODE: {other}", history)

    def test_batch_continues_after_failure_and_resume_retries_only_failed_drug(self):
        from sukl.cli import parser, run

        output_dir = self.root / "batch-failure"
        args = self.batch_args(output_dir) + ["--parallel", "3"]
        calls = []
        failing = "0000010"

        async def offline_extract(prompt, deps, settings, *, trace):
            calls.append(deps.code)

            async def model(messages, info):
                await asyncio.sleep(0)
                if deps.code == failing:
                    raise RuntimeError("mock gateway failure")
                return ModelResponse(parts=[TextPart(json.dumps(self.claim()))])

            return await extract(prompt, deps, settings, model=FunctionModel(model), trace=trace)

        console, output = io.StringIO(), io.StringIO()
        with redirect_stderr(console), redirect_stdout(output), patch("sukl.cli.extract", offline_extract):
            with self.assertRaises(SystemExit) as failure:
                run(parser().parse_args(args))
            self.assertEqual(failure.exception.code, 1)
            self.assertEqual(len(calls), 5)
            self.assertFalse((output_dir / f"{failing}.json").exists())
            debug = json.loads((output_dir / f"{failing}.trace.json").read_text())
            self.assertEqual(debug["status"], "failed")
            self.assertEqual(debug["error"]["message"], "mock gateway failure")
            self.assertIn("4 written, 0 reused, 1 failed", output.getvalue())
            self.assertIn(f"[{failing}] Failed:", console.getvalue())
            failing = None
            run(parser().parse_args([*args, "--resume"]))
        self.assertEqual(calls[5:], ["0000010"])
        self.assertTrue((output_dir / "0000010.json").exists())
        self.assertIn("1 written, 4 reused, 0 failed", output.getvalue())

    def test_id_file_skips_invalid_codes_and_rejects_empty_input_or_conflicting_options(self):
        from sukl.cli import parser, read_codes, run

        ids = self.root / "bad-ids.txt"
        for content in ("# none\n", "unknown\n9999999\n"):
            ids.write_text(content)
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "No SUKL codes"):
                read_codes(ids, self.catalog)
        ids.write_text("9\nunknown\n0000009\n9999999\n")
        warnings = io.StringIO()
        with redirect_stdout(warnings):
            self.assertEqual(read_codes(ids, self.catalog), ["0000009"])
        self.assertIn(f"{ids}:2:", warnings.getvalue())
        self.assertIn("Unknown SUKL code 9999999", warnings.getvalue())
        base = ["--data", str(self.root / "data"), "--ctcae", str(self.ctcae),
                "--ctcae-csv", str(self.ctcae_csv), "--cache", str(self.root / "cli.sqlite"), "extract"]
        with patch("sukl.cli.extract") as model_call:
            for tail in ([], ["9", "--ids-file", str(ids)], ["--ids-file", str(ids), "--output", "single.json"]):
                with self.assertRaises(ValueError):
                    run(parser().parse_args([*base, *tail]))
            model_call.assert_not_called()

    def test_gateway_enforces_native_json_schema_on_both_apis(self):
        good = self.claim()
        cases = [(api, router, custom) for api in ("responses", "chat")
                 for router, custom in ((False, False), (True, False), (True, True))]
        for api, openrouter, custom_body in cases:
            with self.subTest(api=api, openrouter=openrouter, custom_body=custom_body):
                requests = []
                effort = "low" if api == "responses" or openrouter else "none"
                settings = Settings(model="gpt-6-luna-dz-eu", api=api, request_limit=2,
                                    model_settings={"openai_reasoning_effort": effort})
                if openrouter:
                    settings.base_url = "https://openrouter.ai/api/v1/"
                    settings.model = "openai/gpt-6-luna"
                if custom_body:
                    settings.model_settings["openai_reasoning_summary"] = "auto"
                    settings.model_settings["extra_body"] = {
                        "provider": {"require_parameters": True, "only": ["openai"], "allow_fallbacks": True},
                        "reasoning": {"exclude": False, "enabled": False}, "user": "offline-test-user",
                    }
                original_settings = deepcopy(settings.model_settings)

                def handle(request):
                    requests.append(request)
                    body = json.loads(request.content)
                    first = len(requests) == 1
                    self.assertEqual(request.headers["Authorization"], "Bearer offline-test-key")
                    if openrouter:
                        self.assertNotIn("api-key", request.headers)
                        provider = {"only": ["openai/flex"], "allow_fallbacks": False}
                        if custom_body:
                            provider["require_parameters"] = True
                        self.assertEqual(body["provider"], provider)
                        self.assertTrue(body["reasoning"]["enabled"])
                        self.assertEqual(body["reasoning"]["effort"], effort)
                        if custom_body:
                            self.assertEqual(body["reasoning"]["summary"], "auto")
                            self.assertFalse(body["reasoning"]["exclude"])
                            self.assertEqual(body["user"], "offline-test-user")
                    else:
                        self.assertEqual(request.headers["api-key"], "offline-test-key")
                        self.assertNotIn("provider", body)
                        self.assertNotIn("enabled", body.get("reasoning", {}))
                    self.assertEqual(body["model"], settings.model)
                    self.assertNotIn("temperature", body)
                    if api == "responses":
                        self.assertEqual(str(request.url), settings.base_url + "responses")
                        self.assertEqual(body["max_output_tokens"], 24_000)
                        self.assertEqual(body["reasoning"]["effort"], "low")
                        self.assertFalse(body["store"])
                        self.assertNotIn("final_result", [t["name"] for t in body.get("tools", [])])
                        format = body["text"]["format"]
                        if not first:
                            self.assertTrue(any(item.get("type") == "function_call_output"
                                                and item["call_id"] == "call-1" for item in body["input"]))
                            self.assertTrue(any(item.get("type") == "reasoning"
                                                and item.get("encrypted_content") == "opaque-test-reasoning"
                                                for item in body["input"]))
                    else:
                        self.assertEqual(str(request.url), settings.base_url + "chat/completions")
                        token_key = "max_tokens" if openrouter else "max_completion_tokens"
                        self.assertEqual(body[token_key], 24_000)
                        self.assertEqual(body["reasoning_effort"], effort)
                        self.assertEqual(body["response_format"]["type"], "json_schema")
                        self.assertNotIn("final_result", [t["function"]["name"] for t in body.get("tools", [])])
                        format = body["response_format"]["json_schema"]
                        if not first:
                            self.assertTrue(any(item.get("role") == "tool" and item["tool_call_id"] == "call-1"
                                                for item in body["messages"]))
                    if not first:
                        self.assertFalse(body.get("tools"))
                        self.assertIn("No further tools are possible", json.dumps(body))
                    self.assertTrue(format["strict"])
                    schema = format["schema"]
                    self.assertEqual(set(schema["properties"]), {"side_effects", "combinations"})
                    self.assertFalse(schema["additionalProperties"])
                    effect = schema["$defs"]["SideEffect"]
                    self.assertEqual(set(effect["properties"]), {"ctcae", "frequency", "label", "action", "action_condition"})
                    self.assertEqual(set(effect["required"]), set(effect["properties"]))
                    self.assertFalse(effect["additionalProperties"])
                    condition = schema["$defs"]["ActionCondition"]
                    self.assertEqual(condition["required"], ["patient_guide"])
                    self.assertFalse(condition["additionalProperties"])
                    for name in ("DrugCombination", "DrugClassCombination"):
                        self.assertIn("effect", schema["$defs"][name]["required"])
                        self.assertIn("effect_type", schema["$defs"][name]["required"])

                    if api == "responses":
                        if first:
                            output = [
                                {"id": "rs_1", "type": "reasoning", "summary": [],
                                 "encrypted_content": "opaque-test-reasoning"},
                                {"id": "fc_1", "type": "function_call", "call_id": "call-1",
                                 "name": "search_documents", "status": "completed",
                                 "arguments": json.dumps({"query": "Nausea", "kind": "CTCAE"})},
                            ]
                        else:
                            output = [{"id": "msg_2", "type": "message", "role": "assistant", "status": "completed",
                                       "content": [{"type": "output_text", "text": json.dumps(good), "annotations": []}]}]
                        return httpx2.Response(200, json={
                            "id": f"resp_{len(requests)}", "object": "response", "created_at": 1,
                            "model": settings.model, "status": "completed", "output": output,
                            "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                                      "input_tokens_details": {"cached_tokens": 0},
                                      "output_tokens_details": {"reasoning_tokens": 10}},
                        })
                    message = {"role": "assistant", "content": json.dumps(good)}
                    if first:
                        message = {"role": "assistant", "content": None, "tool_calls": [
                            {"id": "call-1", "type": "function", "function": {"name": "search_documents",
                             "arguments": json.dumps({"query": "Nausea", "kind": "CTCAE"})}}]}
                    return httpx2.Response(200, json={
                        "id": f"chatcmpl_{len(requests)}", "object": "chat.completion", "created": 1,
                        "model": settings.model, "choices": [{"index": 0,
                            "finish_reason": "tool_calls" if first else "stop", "message": message}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
                    })

                def client(**kwargs):
                    kwargs["timeout"] = 2.0
                    kwargs["max_retries"] = 0
                    instance = AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(
                        transport=httpx2.MockTransport(handle), trust_env=False,
                    ))
                    instance._platform = "Linux"  # Avoid background platform probing in constrained runners.
                    return instance

                trace = self.root / f"wire-{api}-{openrouter}-{custom_body}.trace.json"
                with patch.dict(os.environ, {"API_KEY": "offline-test-key"}), patch("sukl.extraction.AsyncOpenAI", client):
                    result = asyncio.run(extract(self.prompt, self.deps, settings, trace=trace))
                self.assertEqual(len(requests), 2)
                self.assertEqual(result, good)
                self.assertEqual(settings.model_settings, original_settings)
                self.assertNotIn("offline-test-key", trace.read_text())
                debug = json.loads(trace.read_text())
                self.assertEqual(debug["usage"]["input_tokens"], 200)
                self.assertEqual([r["usage"]["input_tokens"] for r in debug["requests"]], [100, 100])

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler pdftoppm required")
    def test_optional_vision_tool_returns_rendered_image(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("page_image", {"document_id": "PIL:PI1.pdf", "page": 1})])
            images = []
            for part in messages[-1].parts:
                content = getattr(part, "content", None)
                if isinstance(content, BinaryContent):
                    images.append(content)
                elif isinstance(content, list):
                    images.extend(x for x in content if isinstance(x, BinaryContent))
            self.assertEqual(len(images), 1)
            self.assertTrue(images[0].data.startswith(b"\x89PNG"))
            return ModelResponse(parts=[TextPart('{"side_effects": [], "combinations": []}')])

        trace = self.root / "image.trace.json"
        asyncio.run(extract(self.prompt, self.deps, Settings(vision=True), model=FunctionModel(model), trace=trace))
        self.assertEqual(self.deps.image_count, 1)
        call, = json.loads(trace.read_text())["tool_calls"]
        self.assertEqual(call["tool"], "page_image")
        self.assertGreater(call["output_binary_bytes"], 100)


class SchemaTests(unittest.TestCase):
    def test_effect_categories_and_patient_action_condition(self):
        category = TypeAdapter(EffectType | None)
        for value in ("contraindicated", "synergy", "antagonism", "increased_toxicity",
                      "increased_exposure", "decreased_exposure", None):
            self.assertEqual(category.validate_python(value), value)
        with self.assertRaises(ValidationError):
            category.validate_python("invented_mechanism")
        condition = {"patient_guide": "Pokud jste zároveň těhotná"}
        self.assertEqual(ActionCondition.model_validate(condition).model_dump(), condition)
        for invalid in ({"patient_guide": ""}, {"patient_guide": "Pokud...", "fhir": {}}, {"age": 65}):
            with self.assertRaises(ValidationError):
                ActionCondition.model_validate(invalid)

    def test_frequency_is_one_small_value(self):
        frequency = TypeAdapter(Frequency)
        for value in (20, "common", "not_known", None):
            self.assertEqual(frequency.validate_python(value), value)
        for value in (0.05, "made-up-category", {"category": "common"}):
            with self.assertRaises(ValidationError):
                frequency.validate_python(value)

    def test_combination_has_one_target_and_no_extra_fields(self):
        valid = {"side_effects": [], "combinations": [{"drug": "X", "effect": "Zvyšuje toxicitu.", "effect_type": "increased_toxicity", "side_effects": []}]}
        self.assertEqual(DrugExtraction.model_validate(valid).model_dump(), valid)
        valid["combinations"] = [{"drug_class": "Y", "effect": "Snižuje účinnost.", "effect_type": None, "side_effects": []}]
        self.assertEqual(DrugExtraction.model_validate(valid).model_dump(), valid)
        for target in ({"drug": "X", "drug_class": "Y"}, {}):
            with self.assertRaises(ValidationError):
                DrugExtraction.model_validate({"side_effects": [], "combinations": [{**target, "effect": "Popis interakce.", "effect_type": None, "side_effects": []}]})
        with self.assertRaises(ValidationError):
            DrugExtraction.model_validate({"side_effects": [], "combinations": [{"drug": "X", "side_effects": []}]})

    def test_side_effect_needs_no_verbatim_quote(self):
        effect = {"ctcae": None, "frequency": None, "label": "Toxicita perorálních hypoglykemik", "action": None, "action_condition": None}
        self.assertEqual(SideEffect.model_validate(effect).model_dump(), effect)
        with self.assertRaises(ValidationError):
            SideEffect.model_validate({**effect, "evidence": []})


if __name__ == "__main__":
    unittest.main()

