"""Offline integration tests; no credentials or paid models are used."""

import asyncio
from copy import deepcopy
import csv
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
from pydantic_ai.models.function import FunctionModel

from sukl.corpus import Catalog, Corpus
from sukl.extraction import Settings, atomic_json, extract, prepare, validate_coverage
from sukl.schema import DrugExtraction, Frequency, SideEffect


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
        self.catalog = Catalog(data, self.ctcae)
        self.corpus = Corpus(self.catalog, self.root / "cache.sqlite")
        self.settings = Settings()
        self.prompt, self.deps = prepare(self.corpus, "9", self.settings)

    def tearDown(self):
        self.corpus.close()
        self.temp.cleanup()

    def claim(self) -> dict:
        effect = {"ctcae": "Nausea", "frequency": "common",
                  "label": "Nauzea; kontaktujte lékaře při závažných příznacích",
                  "action": "contact_doctor"}
        return {"side_effects": [effect], "combinations": [{"drug": "X", "side_effects": [effect]}]}

    def test_pdf_loading_encoding_and_exact_identity(self):
        self.assertEqual(self.catalog.metadata("9")["product"]["NAZEV"], "LÉČIVO")
        self.assertEqual(self.catalog.code("9"), "0000009")
        self.assertIn("Nausea occurs commonly.", self.prompt)
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
        _, deps = prepare(self.corpus, "9", Settings(context_chars=8))
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
        result = asyncio.run(extract(self.prompt, self.deps, self.settings, model=FunctionModel(model), trace=trace))
        self.assertEqual(calls, 3)
        self.assertEqual(result, good)
        self.assertEqual(set(result), {"side_effects", "combinations"})
        self.assertEqual(set(result["side_effects"][0]), {"ctcae", "frequency", "label", "action"})
        path = self.root / "result.json"
        atomic_json(path, result)
        self.assertEqual(json.loads(path.read_text()), good)
        debug = json.loads(trace.read_text())
        self.assertEqual(debug["usage"]["requests"], 3)
        self.assertIn("messages", debug)

    def test_agent_reads_omitted_page_ranges(self):
        prompt, deps = prepare(self.corpus, "9", Settings(context_chars=8))

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
                "--cache", str(self.root / "cli.sqlite"), "extract", "9", "--output", str(output), "--trace"]
        with patch("sukl.cli.extract", offline_extract):
            run(parser().parse_args(args))
            run(parser().parse_args([*args, "--resume"]))
        self.assertEqual(calls, 1)
        self.assertEqual(json.loads(output.read_text()), good)
        self.assertIn("messages", json.loads(output.with_suffix(".trace.json").read_text()))

    def test_gateway_enforces_native_json_schema_on_both_apis(self):
        good = self.claim()
        for api in ("responses", "chat"):
            with self.subTest(api=api):
                requests = []
                effort = "low" if api == "responses" else "none"
                settings = Settings(model="gpt-6-luna-dz-eu", api=api,
                                    model_settings={"openai_reasoning_effort": effort})

                def handle(request):
                    requests.append(request)
                    body = json.loads(request.content)
                    first = len(requests) == 1
                    self.assertEqual(request.headers["api-key"], "offline-test-key")
                    self.assertEqual(body["model"], settings.model)
                    self.assertNotIn("temperature", body)
                    if api == "responses":
                        self.assertEqual(str(request.url), settings.base_url + "responses")
                        self.assertEqual(body["max_output_tokens"], 24_000)
                        self.assertEqual(body["reasoning"]["effort"], "low")
                        self.assertFalse(body["store"])
                        self.assertNotIn("final_result", [t["name"] for t in body["tools"]])
                        format = body["text"]["format"]
                        if not first:
                            self.assertTrue(any(item.get("type") == "function_call_output"
                                                and item["call_id"] == "call-1" for item in body["input"]))
                            self.assertTrue(any(item.get("type") == "reasoning"
                                                and item.get("encrypted_content") == "opaque-test-reasoning"
                                                for item in body["input"]))
                    else:
                        self.assertEqual(str(request.url), settings.base_url + "chat/completions")
                        self.assertEqual(body["max_completion_tokens"], 24_000)
                        self.assertEqual(body["reasoning_effort"], "none")
                        self.assertEqual(body["response_format"]["type"], "json_schema")
                        self.assertNotIn("final_result", [t["function"]["name"] for t in body["tools"]])
                        format = body["response_format"]["json_schema"]
                        if not first:
                            self.assertTrue(any(item.get("role") == "tool" and item["tool_call_id"] == "call-1"
                                                for item in body["messages"]))
                    self.assertTrue(format["strict"])
                    schema = format["schema"]
                    self.assertEqual(set(schema["properties"]), {"side_effects", "combinations"})
                    self.assertFalse(schema["additionalProperties"])
                    effect = schema["$defs"]["SideEffect"]
                    self.assertEqual(set(effect["properties"]), {"ctcae", "frequency", "label", "action"})
                    self.assertEqual(set(effect["required"]), set(effect["properties"]))
                    self.assertFalse(effect["additionalProperties"])

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

                trace = self.root / f"wire-{api}.trace.json"
                with patch.dict(os.environ, {"API_KEY": "offline-test-key"}), patch("sukl.extraction.AsyncOpenAI", client):
                    result = asyncio.run(extract(self.prompt, self.deps, settings, trace=trace))
                self.assertEqual(len(requests), 2)
                self.assertEqual(result, good)
                self.assertNotIn("offline-test-key", trace.read_text())
                self.assertEqual(json.loads(trace.read_text())["usage"]["input_tokens"], 200)

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

        asyncio.run(extract(self.prompt, self.deps, Settings(vision=True), model=FunctionModel(model)))
        self.assertEqual(self.deps.image_count, 1)


class SchemaTests(unittest.TestCase):
    def test_frequency_is_one_small_value(self):
        frequency = TypeAdapter(Frequency)
        for value in (20, "common", "not_known", None):
            self.assertEqual(frequency.validate_python(value), value)
        for value in (0.05, "made-up-category", {"category": "common"}):
            with self.assertRaises(ValidationError):
                frequency.validate_python(value)

    def test_combination_has_one_target_and_no_extra_fields(self):
        valid = {"side_effects": [], "combinations": [{"drug": "X", "side_effects": []}]}
        self.assertEqual(DrugExtraction.model_validate(valid).model_dump(), valid)
        valid["combinations"] = [{"drug_class": "Y", "side_effects": []}]
        self.assertEqual(DrugExtraction.model_validate(valid).model_dump(), valid)
        for target in ({"drug": "X", "drug_class": "Y"}, {}):
            with self.assertRaises(ValidationError):
                DrugExtraction.model_validate({"side_effects": [], "combinations": [{**target, "side_effects": []}]})

    def test_side_effect_needs_no_verbatim_quote(self):
        effect = {"ctcae": None, "frequency": None, "label": "Toxicita perorálních hypoglykemik", "action": None}
        self.assertEqual(SideEffect.model_validate(effect).model_dump(), effect)
        with self.assertRaises(ValidationError):
            SideEffect.model_validate({**effect, "evidence": []})


if __name__ == "__main__":
    unittest.main()

