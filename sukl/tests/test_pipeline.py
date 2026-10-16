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
import zipfile

from pydantic import ValidationError
import httpx2
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai import BinaryContent
from pydantic_ai.models.function import FunctionModel

from sukl.corpus import Catalog, Corpus
from sukl.extraction import Settings, atomic_json, extract, fingerprint, prepare, validate_evidence
from sukl.schema import Combination, DrugExtraction, Frequency


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
        with zipfile.ZipFile(data / "PIL20260925.zip", "w") as z:
            z.writestr("nested/PI1.pdf", pdf([["Nausea occurs commonly.", "Contact your doctor if severe."], ["Do not combine with drug X."]]))
            z.writestr("legacy.doc", "not a pdf")
        with zipfile.ZipFile(data / "SPC20260925.zip", "w") as z:
            z.writestr("SPC1.pdf", pdf([["Nausea occurs commonly.", "Drug X increases nausea."]]))
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
        return {"side_effects": [{"label": "Nausea", "ctcae": "Nausea",
                "ctcae_evidence": [{"document_id": "CTCAE:ctcae-v6.pdf", "page": 1, "quote": "Nausea Grade 1"}],
                "frequency": {"category": "common", "source_text": "commonly"},
                "action": "contact_doctor", "action_condition": "if severe",
                "action_evidence": [{"document_id": "PIL:PI1.pdf", "page": 1, "quote": "Contact your doctor if severe."}],
                "evidence": [{"document_id": "PIL:PI1.pdf", "page": 1, "quote": "Nausea occurs commonly."}]}],
                "combinations": []}

    def expose_ctcae(self):
        for row in self.corpus.pages("CTCAE:ctcae-v6.pdf"):
            self.deps.expose(row["document_id"], row["page"], 0, len(row["text"]))

    def test_real_pdf_archives_encoding_and_exact_identity(self):
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
            validate_evidence(deps, DrugExtraction(side_effects=[], combinations=[]))
        for doc_id in deps.primary_ids:
            for row in self.corpus.pages(doc_id):
                deps.expose(doc_id, row["page"], 0, len(row["text"]))
        self.assertEqual(deps.unread(), [])
        key = ("PIL:PI1.pdf", 1)
        deps.exposed[key] = [(0, 4), (8, 1000)]
        self.assertEqual(next(x for x in deps.unread() if x["document_id"] == key[0])["start_char"], 4)

    def test_citations_actions_and_ctcae_headings(self):
        self.expose_ctcae()
        good = self.claim()
        validate_evidence(self.deps, DrugExtraction.model_validate(good))
        bad = deepcopy(good)
        bad["side_effects"][0]["evidence"][0]["quote"] = "This quote was fabricated."
        with self.assertRaisesRegex(ValueError, "Quote not found"):
            validate_evidence(self.deps, DrugExtraction.model_validate(bad))
        bad = deepcopy(good)
        bad["side_effects"][0]["action_evidence"] = bad["side_effects"][0]["ctcae_evidence"]
        with self.assertRaisesRegex(ValueError, "target PIL/SPC"):
            validate_evidence(self.deps, DrugExtraction.model_validate(bad))
        bad = deepcopy(good)
        bad["side_effects"][0]["ctcae"] = "A feeling of sickness."
        with self.assertRaisesRegex(ValueError, "term heading"):
            validate_evidence(self.deps, DrugExtraction.model_validate(bad))
        bad = deepcopy(good)
        bad["side_effects"][0]["evidence"] = bad["side_effects"][0]["ctcae_evidence"]
        with self.assertRaisesRegex(ValueError, "at least one target"):
            validate_evidence(self.deps, DrugExtraction.model_validate(bad))

    def test_changed_source_invalidates_text_cache(self):
        ctcae_id = "CTCAE:ctcae-v6.pdf"
        self.ctcae.write_bytes(pdf([["Headache", "Grade 1        Grade 2        Grade 3"]]))
        self.assertTrue(self.corpus.ensure(ctcae_id))
        self.assertEqual(self.corpus.search("Nausea", kind="CTCAE"), [])
        self.assertTrue(self.corpus.search("Headache", kind="CTCAE"))

    def test_resume_fingerprint_tracks_schema_settings_and_sources(self):
        value = fingerprint(self.deps, self.settings)
        self.assertNotEqual(value, fingerprint(self.deps, Settings(model="other")))
        self.assertEqual(value, fingerprint(self.deps, self.settings))
        previous_hash = self.catalog.csv_hashes["dlp_latky.csv"]
        self.catalog.csv_hashes["dlp_latky.csv"] = "changed"
        self.assertNotEqual(value, fingerprint(self.deps, self.settings))
        self.catalog.csv_hashes["dlp_latky.csv"] = previous_hash
        self.ctcae.write_bytes(pdf([["different source"]]))
        self.assertNotEqual(value, fingerprint(self.deps, self.settings))

    def test_full_agent_tools_validation_retry_and_artifact(self):
        calls = 0
        good = self.claim()

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                return ModelResponse(parts=[ToolCallPart("search_documents", {"query": "Nausea", "kind": "CTCAE"})])
            if calls == 2:
                self.assertTrue(any(isinstance(p, ToolReturnPart) for p in messages[-1].parts))
                bad = deepcopy(good)
                bad["side_effects"][0]["evidence"][0]["quote"] = "Fabricated source text."
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, bad)])
            self.assertTrue(any(isinstance(p, RetryPromptPart) for p in messages[-1].parts))
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, good)])

        artifact = asyncio.run(extract(self.prompt, self.deps, self.settings, model=FunctionModel(model), trace=True))
        self.assertEqual(calls, 3)
        self.assertEqual(artifact["extraction"]["side_effects"][0]["ctcae"], "Nausea")
        self.assertEqual(artifact["usage"]["requests"], 3)
        self.assertEqual(artifact["review_status"], "unreviewed")
        path = self.root / "result.json"
        atomic_json(path, artifact)
        self.assertEqual(json.loads(path.read_text())["sukl_code"], "0000009")
        self.assertIn("messages", artifact)

    def test_agent_reads_omitted_page_ranges(self):
        prompt, deps = prepare(self.corpus, "9", Settings(context_chars=8))

        async def model(messages, info):
            unread = deps.unread()
            if unread:
                row = unread[0]
                return ModelResponse(parts=[ToolCallPart("read_pages", {**row, "count": 1})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                    {"side_effects": [], "combinations": []})])

        result = asyncio.run(extract(prompt, deps, self.settings, model=FunctionModel(model)))
        self.assertEqual(deps.unread(), [])
        self.assertEqual(result["usage"]["requests"], 4)

    def test_gateway_wire_format_and_api_key_header_without_network(self):
        requests = []
        good = self.claim()
        good["side_effects"][0]["ctcae"] = None
        good["side_effects"][0]["ctcae_evidence"] = []

        def handle(request):
            requests.append(request)
            body = json.loads(request.content)
            self.assertEqual(str(request.url), self.settings.base_url + "chat/completions")
            self.assertEqual(request.headers["api-key"], "offline-test-key")
            self.assertEqual(body["model"], "gpt-6-luna")
            self.assertEqual(body["max_completion_tokens"], 24_000)
            self.assertNotIn("temperature", body)
            output_tool = next(t for t in body["tools"] if t["function"]["name"] == "final_result")
            self.assertIn("side_effects", output_tool["function"]["parameters"]["properties"])
            return httpx2.Response(200, json={
                "id": "offline-completion", "object": "chat.completion", "created": 1,
                "model": "gpt-6-luna", "choices": [{"index": 0, "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "output-1", "type": "function", "function": {
                        "name": "final_result", "arguments": json.dumps(good)}}]}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
            })

        def client(**kwargs):
            kwargs["timeout"] = 2.0
            kwargs["max_retries"] = 0
            instance = AsyncOpenAI(**kwargs, http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(handle), trust_env=False,
            ))
            # Avoid SDK platform probing in a background thread in constrained runners.
            instance._platform = "Linux"
            return instance

        with patch.dict(os.environ, {"API_KEY": "offline-test-key"}), patch("sukl.extraction.AsyncOpenAI", client):
            artifact = asyncio.run(extract(self.prompt, self.deps, self.settings))
        self.assertEqual(len(requests), 1)
        self.assertNotIn("offline-test-key", json.dumps(artifact))
        self.assertEqual(artifact["usage"]["input_tokens"], 100)
        atomic_json(self.root / "wire-result.json", artifact)

    @unittest.skipUnless(shutil.which("pdftoppm"), "Poppler pdftoppm required")
    def test_optional_vision_tool_returns_rendered_image(self):
        calls = 0

        async def model(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.assertIn("page_image", [tool.name for tool in info.function_tools])
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
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name,
                                                    {"side_effects": [], "combinations": []})])

        asyncio.run(extract(self.prompt, self.deps, Settings(vision=True), model=FunctionModel(model)))
        self.assertEqual(self.deps.image_count, 1)


class SchemaTests(unittest.TestCase):
    def test_frequency_does_not_invent_points(self):
        common = Frequency(category="common", source_text="common")
        self.assertIsNone(common.one_in)
        self.assertEqual(common.interval, (0.01, 0.1, True, False))
        point = Frequency(category="exact", source_text="5%", exact_probability=0.05)
        self.assertEqual(point.one_in, 20)
        with self.assertRaises(ValidationError):
            Frequency(category="common", source_text="common", exact_probability=0.1)
        with self.assertRaises(ValidationError):
            Frequency(category="exact", source_text="5%")

    def test_combination_target_is_exclusive(self):
        citation = {"document_id": "SPC:1.pdf", "page": 1, "quote": "Drug interaction."}
        with self.assertRaises(ValidationError):
            Combination(drug="X", drug_class="Y", effect="increased exposure", evidence=[citation])
        with self.assertRaises(ValidationError):
            Combination(effect="increased exposure", evidence=[citation])


if __name__ == "__main__":
    unittest.main()
