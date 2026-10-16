
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(
    title="Questions API",
    version="0.1.0",
)

# Allow requests from your Svelte development server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)

# Paths
BASE_DIR = Path(__file__).parent
QUESTIONS_DIR = BASE_DIR / "questions"
QUESTION_IDS_FILE = BASE_DIR / "question_ids.json"
ANSWERS_FILE = BASE_DIR / "answers.json"


# Request models for submitted answers
class SubmittedAnswer(BaseModel):
    question_id: str | int
    question: str
    answer: str | None = None


class AnswerSubmission(BaseModel):
    answers: list[SubmittedAnswer]


def load_questions():
    """Load questions from all JSON files in the questions directory."""
    QUESTIONS_DIR.mkdir(parents=True, exist_ok=True)
    questions = []

    for file_path in sorted(QUESTIONS_DIR.glob("*.json")):
        try:
            data = json.loads(
                file_path.read_text(encoding="utf-8")
            )

            if isinstance(data, list):
                questions.extend(data)
            elif isinstance(data, dict):
                questions.append(data)
            else:
                raise ValueError(
                    "Expected a JSON object or list"
                )

        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Invalid question file "
                    f"{file_path.name}: {exc}"
                ),
            ) from exc

    return questions


def load_question_ids():
    """Load the IDs of questions that should be included."""
    if not QUESTION_IDS_FILE.exists():
        raise HTTPException(
            status_code=500,
            detail="question_ids.json not found",
        )

    try:
        data = json.loads(
            QUESTION_IDS_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, list):
            raise ValueError("Expected a JSON list of IDs")

        if not all(
            isinstance(item, (str, int))
            and not isinstance(item, bool)
            for item in data
        ):
            raise ValueError(
                "Every question ID must be a string or integer"
            )

        return {str(question_id) for question_id in data}

    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid question_ids.json: {exc}",
        ) from exc


def get_filtered_questions():
    """Return only questions whose IDs are in question_ids.json."""
    questions = load_questions()
    allowed_ids = load_question_ids()

    return [
        question
        for question in questions
        if str(question.get("id", "")) in allowed_ids
    ]


@app.get("/")
def root():
    return {"message": "Questions API is running"}


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/questions")
def get_questions(
    q: str = Query(default="", max_length=200)
):
    # First filter by the IDs in question_ids.json
    questions = get_filtered_questions()

    # Then optionally filter by question text
    query = q.casefold().strip()

    if query:
        questions = [
            item
            for item in questions
            if query in str(
                item.get("question", "")
            ).casefold()
        ]

    return {
        "count": len(questions),
        "results": questions,
    }


@app.get("/api/questions/{question_id}")
def get_question(question_id: str):
    # Search only among questions allowed by question_ids.json
    for question in get_filtered_questions():
        if str(question.get("id", "")) == question_id:
            return question

    raise HTTPException(
        status_code=404,
        detail="Question not found",
    )


@app.post("/api/answers")
def save_answers(submission: AnswerSubmission):
    """Save submitted answers to answers.json."""

    # Create the file if it doesn't exist.
    if not ANSWERS_FILE.exists():
        try:
            ANSWERS_FILE.write_text(
                "[]",
                encoding="utf-8",
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="Could not create answers.json.",
            ) from exc

    # Load previously saved submissions.
    try:
        existing_data = json.loads(
            ANSWERS_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(existing_data, list):
            raise ValueError("Expected a JSON list")

    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid answers.json: {exc}",
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not read answers.json.",
        ) from exc

    # Create a unique ID for this submission.
    submission_id = str(uuid4())

    # Build the record to save.
    record = {
        "submission_id": submission_id,
        "submitted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "answers": [
            answer.model_dump()
            for answer in submission.answers
        ],
    }

    # Append the new submission to the existing records.
    existing_data.append(record)

    # Save all submissions to the JSON file.
    try:
        ANSWERS_FILE.write_text(
            json.dumps(
                existing_data,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail="Could not write answers to answers.json.",
        ) from exc

    return {
        "message": "Answers saved successfully",
        "submission_id": submission_id,
    }
