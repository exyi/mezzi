"""Patient questionnaire web app: FastAPI backend serving the Svelte frontend.

Run from this directory:

    uvicorn main:app --reload

Then open http://127.0.0.1:8000/
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from core import ValidationError, build_submission, parse_questionnaire, save_submission

BASE_DIR = Path(__file__).resolve().parent
RESPONSES_DIR = BASE_DIR / "responses"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Patient questionnaire")


@app.post("/api/questionnaire")
async def upload_questionnaire(file: UploadFile) -> dict:
    """Upload a questionnaire JSON file; validate it and return the parsed form."""
    raw = await file.read()
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=f"file is not valid JSON: {exc}") from exc
    try:
        return parse_questionnaire(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/responses")
async def submit_responses(payload: dict) -> dict:
    """Validate answers for the whole questionnaire and save the response."""
    try:
        questionnaire = parse_questionnaire(payload.get("questionnaire"))
        submission = build_submission(questionnaire, payload.get("answers"))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    path = save_submission(submission, RESPONSES_DIR)
    return {
        "filename": path.name,
        "bad": submission["bad"],
        "maxGrade": submission["maxGrade"],
    }


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
