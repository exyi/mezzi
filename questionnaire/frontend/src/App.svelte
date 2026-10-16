<script>
  let questionnaire = $state(null);
  let answers = $state({});
  let error = $state("");
  let result = $state(null);
  let busy = $state(false);

  async function handleFile(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    error = "";
    result = null;
    busy = true;
    try {
      const body = new FormData();
      body.append("file", file);
      const res = await fetch("/api/questionnaire", { method: "POST", body });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Upload failed");
      questionnaire = data;
      answers = {};
    } catch (e) {
      error = e.message;
      questionnaire = null;
    } finally {
      busy = false;
    }
  }

  let allAnswered = $derived(
    questionnaire && questionnaire.questions.every((q) => answers[q.id] !== undefined)
  );

  async function submit() {
    error = "";
    busy = true;
    try {
      const res = await fetch("/api/responses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ questionnaire, answers }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Submission failed");
      result = data;
    } catch (e) {
      error = e.message;
    } finally {
      busy = false;
    }
  }

  function reset() {
    questionnaire = null;
    answers = {};
    result = null;
    error = "";
  }
</script>

<main>
  <h1>Patient questionnaire</h1>

  {#if error}
    <p class="error">{error}</p>
  {/if}

  {#if result}
    <div class="card">
      <h2>Answers saved</h2>
      <p>
        Saved as <span class="filename">{result.filename}</span>
      </p>
      {#if result.bad}
        <p class="bad-flag">
          At least one answer was graded {">= 2"}: this response was flagged (BAD).
        </p>
      {:else}
        <p class="ok-flag">No answer was graded &gt;= 2.</p>
      {/if}
      <button class="secondary" onclick={reset}>Start over</button>
    </div>
  {:else if !questionnaire}
    <div class="card">
      <p>Upload the patient's questionnaire file (.json) to begin.</p>
      <input type="file" accept=".json,application/json" onchange={handleFile} disabled={busy} />
    </div>
  {:else}
    <div class="card">
      <p>
        Patient <strong>{questionnaire.patientID}</strong> &mdash;
        {questionnaire.questions.length}
        {questionnaire.questions.length === 1 ? "question" : "questions"}. Choose one answer
        per question.
      </p>
    </div>

    <form
      onsubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      {#each questionnaire.questions as question (question.id)}
        <fieldset class="card question">
          <p>{question.text}</p>
          {#each question.answers as answer, index (index)}
            <label class="answer" class:selected={answers[question.id] === index}>
              <input
                type="radio"
                name={question.id}
                value={index}
                checked={answers[question.id] === index}
                onchange={() => (answers[question.id] = index)}
              />
              {answer.text}
            </label>
          {/each}
        </fieldset>
      {/each}

      <button type="submit" disabled={!allAnswered || busy}>Submit answers</button>
      {#if !allAnswered && !busy}
        <span class="muted"> Answer all questions to submit.</span>
      {/if}
    </form>
  {/if}
</main>
