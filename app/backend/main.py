
from pathlib import Path
import json

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

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
    allow_methods=["GET"],
    allow_headers=["Content-Type", "Authorization"],
)

QUESTIONS_DIR = Path(__file__).parent / "questions"


def load_questions():
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
    questions = load_questions()
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
    for question in load_questions():
        if str(question.get("id", "")) == question_id:
            return question

    raise HTTPException(
        status_code=404,
        detail="Question not found",
    )
