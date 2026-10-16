# Patient questionnaire

A small web app that collects medical data through a personalized questionnaire
delivered as a JSON file.

- **Backend**: FastAPI (`main.py`, logic in `core.py`)
- **Frontend**: Svelte 5 (source in `frontend/`, pre-built bundle in `static/`)

## Questionnaire file format

```json
{
  "patientID": "P12345",
  "questions": [
    {
      "id": "q1",
      "text": "How do you feel overall today?",
      "answers": [
        { "text": "I feel good", "grade": 1 },
        { "text": "I feel ok", "grade": 2 },
        { "text": "I feel bad", "grade": 3 },
        { "text": "I feel very bad", "grade": 4 }
      ]
    }
  ]
}
```

- `records` is accepted as an alias for `questions`, `question` for a
  question's `text`, and `label` for an answer's `text`.
- `id` is optional (auto-numbered `q1`, `q2`, ...) but must be unique when present.
- Answer `grade` is an integer 1-4. Exactly one answer can be selected per question.
- See `example_questionnaire.json` for a complete example.

## Behaviour

1. The user uploads the questionnaire JSON on the start page.
2. All questions are shown on one page; one answer per question (radio buttons).
3. On submit, the response is saved to `responses/`:

   - `responses/P12345_2026-10-16.json` — normal response
   - `responses/P12345_2026-10-16_BAD.json` — **at least one chosen answer has
     grade >= 2** (`BAD_GRADE_THRESHOLD` in `core.py`)

   Saved content: patientID, date, `bad` flag, max grade, and every question
   with its chosen answer text and grade. Existing files are never overwritten;
   a repeat submission the same day gets `_2`, `_3`, ... appended to the stem.

## Run

Backend (Python 3.13+):

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Then open <http://127.0.0.1:8000/>.

Frontend development (requires Node/npm):

```bash
cd frontend
npm install
npm run dev        # Vite dev server on :5173, proxies /api to :8000
npm run build       # rebuilds ../static served by FastAPI
```

The committed `static/` build means Node is **not** needed to run the app.

## Tests

```bash
python3 -m unittest -v
```

Covers questionnaire validation, the grade >= 2 flagging rule, file naming,
patientID sanitization and no-overwrite saving.
