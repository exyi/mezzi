"""Tests for questionnaire core logic: parsing, grading flags and file saving."""

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from core import (
    BAD_GRADE_THRESHOLD,
    ValidationError,
    build_submission,
    parse_questionnaire,
    response_filename,
    save_submission,
)


def make_questionnaire(grades=(1, 1, 1, 1)):
    return {
        "patientID": "P1",
        "questions": [
            {
                "id": "q1",
                "text": "How do you feel?",
                "answers": [
                    {"text": f"answer {grade}", "grade": grade} for grade in grades
                ],
            }
        ],
    }


class ParseQuestionnaireTest(unittest.TestCase):
    def test_valid(self):
        parsed = parse_questionnaire(make_questionnaire())
        self.assertEqual(parsed["patientID"], "P1")
        self.assertEqual(len(parsed["questions"]), 1)
        self.assertEqual(parsed["questions"][0]["id"], "q1")
        self.assertEqual(len(parsed["questions"][0]["answers"]), 4)

    def test_defaults_and_aliases(self):
        parsed = parse_questionnaire(
            {
                "patientID": " P1 ",
                "records": [{"question": "Q?", "answers": [{"label": "a", "grade": 1}]}],
            }
        )
        self.assertEqual(parsed["patientID"], "P1")
        self.assertEqual(parsed["questions"][0]["id"], "q1")
        self.assertEqual(parsed["questions"][0]["text"], "Q?")
        self.assertEqual(parsed["questions"][0]["answers"][0]["text"], "a")

    def test_missing_patient_id(self):
        with self.assertRaises(ValidationError):
            parse_questionnaire({"questions": []})

    def test_empty_questions(self):
        with self.assertRaises(ValidationError):
            parse_questionnaire({"patientID": "P1", "questions": []})

    def test_bad_grade(self):
        with self.assertRaises(ValidationError):
            parse_questionnaire(make_questionnaire(grades=(0, 1, 2, 3)))
        with self.assertRaises(ValidationError):
            parse_questionnaire(make_questionnaire(grades=(1, 2, 3, 5)))

    def test_duplicate_question_ids(self):
        data = make_questionnaire()
        data["questions"].append(dict(data["questions"][0]))
        with self.assertRaises(ValidationError):
            parse_questionnaire(data)

    def test_not_an_object(self):
        with self.assertRaises(ValidationError):
            parse_questionnaire([1, 2, 3])


class BuildSubmissionTest(unittest.TestCase):
    def submit(self, index, grades=(1, 1, 1, 1)):
        questionnaire = parse_questionnaire(make_questionnaire(grades=grades))
        return build_submission(questionnaire, {"q1": index})

    def test_no_flag_when_all_below_threshold(self):
        submission = self.submit(0, grades=(1, 2, 3, 4))
        # grade 1 < BAD_GRADE_THRESHOLD
        self.assertFalse(submission["bad"])
        self.assertEqual(submission["maxGrade"], 1)
        self.assertEqual(submission["date"], date.today().isoformat())

    def test_flag_when_grade_reaches_threshold(self):
        submission = self.submit(1, grades=(1, 2, 3, 4))
        self.assertTrue(submission["bad"])
        self.assertEqual(submission["maxGrade"], 2)
        self.assertGreaterEqual(2, BAD_GRADE_THRESHOLD)

    def test_flag_on_highest_grade(self):
        submission = self.submit(3, grades=(1, 2, 3, 4))
        self.assertTrue(submission["bad"])
        self.assertEqual(submission["maxGrade"], 4)

    def test_missing_answer_rejected(self):
        questionnaire = parse_questionnaire(make_questionnaire())
        with self.assertRaises(ValidationError):
            build_submission(questionnaire, {})

    def test_out_of_range_answer_rejected(self):
        questionnaire = parse_questionnaire(make_questionnaire())
        with self.assertRaises(ValidationError):
            build_submission(questionnaire, {"q1": 4})
        with self.assertRaises(ValidationError):
            build_submission(questionnaire, {"q1": -1})

    def test_multiple_questions_any_bad_flags(self):
        data = make_questionnaire()
        data["questions"].append(
            {
                "id": "q2",
                "text": "Pain?",
                "answers": [{"text": "no", "grade": 1}, {"text": "yes", "grade": 3}],
            }
        )
        questionnaire = parse_questionnaire(data)
        submission = build_submission(questionnaire, {"q1": 0, "q2": 0})
        self.assertFalse(submission["bad"])
        submission = build_submission(questionnaire, {"q1": 0, "q2": 1})
        self.assertTrue(submission["bad"])


class SaveSubmissionTest(unittest.TestCase):
    def make_submission(self, bad=False, patient_id="P1"):
        questionnaire = parse_questionnaire(make_questionnaire(grades=(1, 2, 3, 4)))
        submission = build_submission(questionnaire, {"q1": 2 if bad else 0})
        submission["patientID"] = patient_id
        return submission

    def test_filename_format(self):
        submission = self.make_submission()
        name = response_filename(submission)
        self.assertEqual(name, f"P1_{date.today().isoformat()}.json")
        submission = self.make_submission(bad=True)
        name = response_filename(submission)
        self.assertEqual(name, f"P1_{date.today().isoformat()}_BAD.json")

    def test_patient_id_sanitized(self):
        submission = self.make_submission(patient_id="../evil id/1")
        name = response_filename(submission)
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.startswith("___evil_id_1_") or name[0] != ".")

    def test_save_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            responses_dir = Path(tmp)
            first = save_submission(self.make_submission(bad=True), responses_dir)
            self.assertTrue(first.exists())
            self.assertTrue(first.name.endswith("_BAD.json"))
            saved = json.loads(first.read_text(encoding="utf-8"))
            self.assertTrue(saved["bad"])
            self.assertEqual(saved["patientID"], "P1")
            second = save_submission(self.make_submission(bad=True), responses_dir)
            self.assertNotEqual(first, second)
            self.assertEqual(len(list(responses_dir.iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
