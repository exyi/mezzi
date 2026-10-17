"""Subscription-backed extraction through the authenticated Codex CLI."""

import asyncio
import json
from pathlib import Path
import sys
import tempfile

from .ctcae import validation_warnings
from .prompts import INSTRUCTIONS
from .schema import DrugExtraction, DrugOutput, FUNCTIONAL_CLASSES


async def extract_codex(prompt, deps, settings, trace: Path | None = None) -> dict:
    if deps.unread():
        raise ValueError("Codex requires all primary document text in the prepared context")
    if settings.vision or settings.model_settings:
        raise ValueError("--vision and --model-settings are not supported with --api codex")
    instructions = (
        INSTRUCTIONS
        + "\nThis run uses Codex. All primary pages are included. The Python agent tools "
        "are unavailable. For find_drugs, use `uv run sukl find QUERY` in the shell "
        "to verify substance IDs from CSV metadata. Do not change files, access the "
        "network, or run other model calls. Return the final extraction JSON.\n\n"
        + prompt
    )
    with tempfile.TemporaryDirectory(prefix="sukl-codex-") as directory:
        schema = Path(directory) / "schema.json"
        output = Path(directory) / "result.json"
        schema.write_text(json.dumps(DrugExtraction.model_json_schema()), encoding="utf-8")
        command = [
            "codex", "exec", "--ignore-user-config", "--ephemeral",
            "--model", settings.model, "--sandbox", "read-only", "--json",
            "--output-schema", str(schema), "--output-last-message", str(output), "-",
        ]
        events = []
        error = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await process.communicate(instructions.encode())
            except BaseException:
                process.kill()
                await process.wait()
                raise
            for line in stdout.decode().splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            if process.returncode:
                raise ValueError(f"Codex exited {process.returncode}: "
                                 + stderr.decode()[-3000:] + stdout.decode()[-3000:])
            result = DrugExtraction.model_validate_json(output.read_text(encoding="utf-8"))
            catalog = deps.corpus.catalog
            for combination in result.combinations:
                identifier = combination.class_id
                if identifier is None:
                    continue
                namespace, value = identifier.split(":", 1)
                vocabulary = {"atc": catalog.dictionaries["atc"],
                              "substance": catalog.substances, "group": FUNCTIONAL_CLASSES}[namespace]
                if (identifier if namespace == "group" else value) not in vocabulary:
                    raise ValueError(f"Unknown class_id {identifier}")
            deps.validation_warnings = validation_warnings(result, deps.ctcae_terms)
            for warning in deps.validation_warnings:
                print(f"[{deps.code}] Warning: {warning}", file=sys.stderr, flush=True)
            return DrugOutput.model_validate(catalog.drug_output(
                deps.code, result.model_dump(mode="json"))).model_dump(mode="json")
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            if trace is not None:
                from .extraction import atomic_json
                atomic_json(trace, {"code": deps.code, "model": settings.model,
                                    "backend": "codex", "status": "failed" if error else "completed",
                                    "error": error, "events": events,
                                    "validation_warnings": deps.validation_warnings})
