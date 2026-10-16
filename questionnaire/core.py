"""Questionnaire parsing, grading and response saving logic.

Input questionnaire JSON format (see example_questionnaire.json):

    {
      "patientID": "P12345",
      "questions": [
        {
          "id": "q1",              # optional, must be unique when present
          "text": "How do you feel overall?",
          "answers": [
            {"text": "I feel good", "grade": 1},
            {"text": "I feel ok", "grade": 2},
            {"text": "I feel bad", "grade": 3},
            {"text": "I feel very bad", "grade": 4}
          ]
        }
      ]
    }

"records" is accepted as an alias for "questions", "question" for the
question "text" and "label" for an answer "text".

Every question offers exactly one choice; grades are integers 1-4. A saved
response is flagged when at least one chosen answer has grade >=
BAD_GRADE_THRESHOLD, and the file name gets a "_BAD" suffix.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

MIN_GRADE = 1
MAX_GRADE = 4
BAD_GRADE_THRESHOLD = 2  # any chosen answer with grade >= this flags the response

_UNSAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
MAX_ID_LEN = 64


class ValidationError(ValueError):
    """Raised when a questionnaire or the submitted answers are invalid."""


def parse_questionnaire(data: object) -> dict:
    """Validate a raw questionnaire object and return a normalized copy."""
    if not isinstance(data, dict):
        raise ValidationError("questionnaire must be a JSON object")
    patient_id = data.get("patientID")
    if not isinstance(patient_id, str) or not patient_id.strip():
        raise ValidationError("'patientID' must be a non-empty string")
    questions_raw = data.get("questions", data.get("records"))
    if not isinstance(questions_raw, list) or not questions_raw:
        raise ValidationError("'questions' must be a non-empty list")
    questions = []
    seen_ids: set[str] = set()
    for i, question in enumerate(questions_raw, start=1):
        if not isinstance(question, dict):
            raise ValidationError(f"question #{i} must be a JSON object")
        text = question.get("text", question.get("question"))
        if not isinstance(text, str) or not text.strip():
            raise ValidationError(f"question #{i} needs a non-empty 'text'")
        answers_raw = question.get("answers")
        if not isinstance(answers_raw, list) or not answers_raw:
            raise ValidationError(f"question #{i} needs a non-empty 'answers' list")
        answers = []
        for j, answer in enumerate(answers_raw, start=1):
            if not isinstance(answer, dict):
                raise ValidationError(f"answer #{j} of question #{i} must be a JSON object")
            answer_text = answer.get("text", answer.get("label"))
            if not isinstance(answer_text, str) or not answer_text.strip():
                raise ValidationError(f"answer #{j} of question #{i} needs a non-empty 'text'")
            grade = answer.get("grade")
            if (
                not isinstance(grade, int)
                or isinstance(grade, bool)
                or not MIN_GRADE <= grade <= MAX_GRADE
            ):
                raise ValidationError(
                    f"answer #{j} of question #{i} needs an integer 'grade' "
                    f"between {MIN_GRADE} and {MAX_GRADE}"
                )
            answers.append({"text": answer_text, "grade": grade})
        qid = question.get("id") or f"q{i}"
        if not isinstance(qid, str) or not qid.strip():
            raise ValidationError(f"question #{i} 'id' must be a non-empty string")
        qid = str(qid)
        if qid in seen_ids:
            raise ValidationError(f"duplicate question id '{qid}'")
        seen_ids.add(qid)
        questions.append({"id": qid, "text": text, "answers": answers})
    return {"patientID": patient_id.strip(), "questions": questions}


def build_submission(questionnaire: dict, answer_indices: object) -> dict:
    """Validate one chosen answer per question and build the saved record."""
    if not isinstance(answer_indices, dict):
        raise ValidationError("'answers' must be an object mapping question id to answer index")
    chosen = []
    for question in questionnaire["questions"]:
        qid = question["id"]
        if qid not in answer_indices:
            raise ValidationError(f"question '{qid}' is not answered")
        index = answer_indices[qid]
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValidationError(f"answer for question '{qid}' must be an answer index")
        if not 0 <= index < len(question["answers"]):
            raise ValidationError(f"answer for question '{qid}' is out of range")
        answer = question["answers"][index]
        chosen.append(
            {"question": question["text"], "answer": answer["text"], "grade": answer["grade"]}
        )
    grades = [answer["grade"] for answer in chosen]
    return {
        "patientID": questionnaire["patientID"],
        "date": date.today().isoformat(),
        "bad": any(grade >= BAD_GRADE_THRESHOLD for grade in grades),
        "maxGrade": max(grades),
        "answers": chosen,
    }


def safe_patient_id(patient_id: str) -> str:
    """Reduce a patient id to characters safe for a file name."""
    cleaned = _UNSAFE_ID.sub("_", patient_id.strip())[:MAX_ID_LEN].strip("._-")
    return cleaned or "unknown"


def response_filename(submission: dict) -> str:
    """Build patientID_DATE (or patientID_DATE_BAD) file name."""
    stem = f"{safe_patient_id(submission['patientID'])}_{submission['date']}"
    if submission["bad"]:
        stem += "_BAD"
    return stem + ".json"


def save_submission(submission: dict, responses_dir: Path) -> Path:
    """Write the submission JSON into responses_dir; never overwrite a file."""
    responses_dir.mkdir(parents=True, exist_ok=True)
    path = responses_dir / response_filename(submission)
    counter = 2
    while path.exists():
        path = responses_dir / f"{path.stem}_{counter}.json"
        counter += 1
    path.write_text(
        json.dumps(submission, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path
