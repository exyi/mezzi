# mezzi app setup


## How to run

* Install Python dependencies
    ```sh
    uv sync
    ```
* Prepare questionare for your FHIR record
    ```sh
    uv run python -m src.create_questionare_json PAC-1234.json --as-of 2026-03-22 > example-questionare.json
    ```
    You can exclude `--as-of` if you want to assume current time. For demonstration that's not ideal, since most medication in test dataset are long expired.
* Start backend
    ```sh
    cd app/backend/
    uv run -m uvicorn main:app
    ```
* Start patient frontend:
    ```sh
    npm ci
    npm run dev
    ```
* Fill out questionare, probably at `http://localhost:5173/`
* Start doctor frontend:
    ```sh
    npm ci
    npm run dev
    ```
* See the results, probably at `http://localhost:5174/`


## Other notes

* You can specify multiple FHIR records at the commandline (assumed to have one patient)
* Output of `create_questionare_json` a JSON object `{"patient": {"gender": ...}, "questions": [...]}` where `questions` is an array matching the data displayed in frontend.
  **The web application never sees the health record, only the list of potential side effects.**
* If multiple prescriptions are found, system tries to include side effects from known drug combinations.
* Definiton of side effects and combinations comes from scraping SUKL database:
  ```sh
  cd sukl
  ./download-sukl.sh
  export OPENAI_BASE_URL=YOUR_PROVIDER
  export API_KEY=YOUR_KEY
  uv run sukl extract --ids-file relevant_ids.txt --model gpt-6-luna --parallelism 30 --resume --trace
  ```
* The pre-generated files are created using GPT 6 Luna. Cost of running on relevant_ids.txt is few dollars.
