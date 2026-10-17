from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


app = FastAPI(
    title="Medical Terminology Questions API",
    version="0.4.0",
)


# --------------------------------------------------
# CORS CONFIGURATION
# --------------------------------------------------

# Allow requests from your Svelte development server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --------------------------------------------------
# FILE PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).parent
QUESTIONS_DIR = BASE_DIR / "questions"
QUESTION_IDS_FILE = BASE_DIR / "question_ids.json"
ANSWERS_FILE = BASE_DIR / "answers.json"


# --------------------------------------------------
# MODELS: MEDICAL QUESTIONS
# --------------------------------------------------

class Grade(BaseModel):
    level: int
    description: str | None = None


class Question(BaseModel):
    id: str | int
    llt_code: int
    soc: str
    term: str
    grades: list[Grade]
    definition: str | None = None
    navigational_note: str | None = None
    change: str | None = None


# --------------------------------------------------
# MODELS: SUBMITTED ANSWERS
# --------------------------------------------------

class SubmittedAnswer(BaseModel):
    question_id: str | int
    llt_code: int
    soc: str
    term: str
    definition: str | None = None
    answer: int
    grade_description: str | None = None


class AnswerSubmission(BaseModel):
    answers: list[SubmittedAnswer]


# --------------------------------------------------
# LOAD QUESTIONS
# --------------------------------------------------

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

            elif isinstance(data, dict) and isinstance(
                data.get("questions"), list
            ):
                # generated questionnaire: {"patient": {...}, "questions": [...]}
                questions.extend(data["questions"])

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


# --------------------------------------------------
# LOAD ENABLED QUESTION IDS
# --------------------------------------------------

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
            raise ValueError(
                "Expected a JSON list of IDs"
            )

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


# --------------------------------------------------
# FILTER QUESTIONS AND ADD GRADE 0
# --------------------------------------------------

def get_filtered_questions():
    """
    Return enabled medical questions and add Grade 0
    without modifying the original question JSON files.
    """

    questions = load_questions()
    allowed_ids = load_question_ids()

    filtered_questions = [
        question
        for question in questions
        if isinstance(question, dict)
        and str(question.get("id", "")) in allowed_ids
        and "llt_code" in question
        and "soc" in question
        and "term" in question
        and isinstance(question.get("grades"), list)
    ]

    for question in filtered_questions:
        # Copy the grades list to avoid modifying loaded data.
        grades = list(question["grades"])

        has_grade_zero = any(
            isinstance(grade, dict)
            and grade.get("level") == 0
            for grade in grades
        )

        if not has_grade_zero:
            grades.insert(
                0,
                {
                    "level": 0,
                    "description": "No symptoms.",
                },
            )

        question["grades"] = grades

    return filtered_questions


# --------------------------------------------------
# API ROOT
# --------------------------------------------------

@app.get("/")
def root():
    return {
        "message": (
            "Medical Terminology Questions API is running"
        )
    }


# --------------------------------------------------
# HEALTH CHECK
# --------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok"}


# --------------------------------------------------
# GET ALL QUESTIONS / SEARCH QUESTIONS
# --------------------------------------------------

@app.get("/api/questions")
def get_questions(
    q: str = Query(default="", max_length=200)
):
    """Return enabled questions, optionally matching a search query."""

    questions = get_filtered_questions()
    query = q.casefold().strip()

    if query:

        def matches(question):
            searchable_fields = [
                question.get("id", ""),
                question.get("llt_code", ""),
                question.get("soc", ""),
                question.get("term", ""),
                question.get("definition") or "",
                question.get("navigational_note") or "",
                question.get("change") or "",
            ]

            return any(
                query in str(value).casefold()
                for value in searchable_fields
            )

        questions = [
            question
            for question in questions
            if matches(question)
        ]

    return {
        "count": len(questions),
        "results": questions,
    }


# --------------------------------------------------
# GET ONE QUESTION
# --------------------------------------------------

@app.get("/api/questions/{question_id}")
def get_question(question_id: str):
    """Return one enabled medical question."""

    for question in get_filtered_questions():
        if str(question.get("id", "")) == question_id:
            return question

    raise HTTPException(
        status_code=404,
        detail="Question not found",
    )


# --------------------------------------------------
# GET SAVED QUESTIONNAIRE SUBMISSIONS
# --------------------------------------------------

@app.get("/api/submissions")
def get_submissions():
    """
    Return saved questionnaire submissions for the
    Svelte Doctor Dashboard, newest first.
    """

    if not ANSWERS_FILE.exists():
        return {
            "count": 0,
            "results": [],
        }

    try:
        data = json.loads(
            ANSWERS_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(data, list):
            raise ValueError(
                "Expected a JSON list"
            )

        # Sort newest submissions first.
        data.sort(
            key=lambda item: (
                item.get("submitted_at", "")
                if isinstance(item, dict)
                else ""
            ),
            reverse=True,
        )

        return {
            "count": len(data),
            "results": data,
        }

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


# --------------------------------------------------
# SAVE QUESTIONNAIRE ANSWERS
# --------------------------------------------------

@app.post("/api/answers")
def save_answers(submission: AnswerSubmission):
    """Validate and save submitted questionnaire answers."""

    # Accept grades from 0 through 5.
    for answer in submission.answers:
        if answer.answer not in range(0, 6):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Each answer must be a grade "
                    "between 0 and 5."
                ),
            )

    # Create the answers file if it does not exist.
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

    # Load previous submissions.
    try:
        existing_data = json.loads(
            ANSWERS_FILE.read_text(encoding="utf-8")
        )

        if not isinstance(existing_data, list):
            raise ValueError(
                "Expected a JSON list"
            )

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

    # Generate a unique submission ID.
    submission_id = str(uuid4())

    # Build the record and provide a default description
    # when a Grade 0 answer has no description.
    record = {
        "submission_id": submission_id,
        "submitted_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "answers": [
            {
                **answer.model_dump(),
                "grade_description": (
                    answer.grade_description
                    or (
                        "No symptoms."
                        if answer.answer == 0
                        else None
                    )
                ),
            }
            for answer in submission.answers
        ],
    }

    # Append without replacing earlier submissions.
    existing_data.append(record)

    # Save all submissions.
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
