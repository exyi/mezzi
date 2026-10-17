
<script lang="ts">
  import { onMount } from 'svelte';

  type Grade = {
    level: number;
    description: string | null;
  };

  type Question = {
    id: string | number;
    llt_code: number;
    soc: string;
    term: string;
    grades: Grade[];
    definition: string | null;
    navigational_note: string | null;
    change: string | null;
  };

  type SubmittedAnswer = {
    question_id: string | number;
    llt_code: number;
    soc: string;
    term: string;
    definition: string | null;
    answer: number;
    grade_description: string | null;
  };

  type SubmissionPayload = {
    symptoms: string;
    answers: SubmittedAnswer[];
  };

  const API_URL = 'http://127.0.0.1:8000/api/questions';
  const ANSWERS_URL = 'http://127.0.0.1:8000/api/answers';

  let questions = $state<Question[]>([]);
  let answers = $state<Record<number, number>>({});
  let symptoms = $state('');
  let currentIndex = $state(0);

  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');
  let saveError = $state('');
  let saved = $state(false);
  let submissionId = $state('');

  let currentQuestion = $derived(questions[currentIndex]);
  let totalQuestions = $derived(questions.length);

  let progress = $derived(
    totalQuestions > 0
      ? (Math.min(currentIndex + 1, totalQuestions) /
          totalQuestions) * 100
      : 0
  );

  // The final symptoms step is after all medical questions.
  let symptomsStep = $derived(
    totalQuestions > 0 && currentIndex === totalQuestions
  );

  // Results appear after the symptoms step has been submitted.
  let finished = $derived(
    totalQuestions > 0 && currentIndex > totalQuestions
  );

  let answeredCount = $derived(Object.keys(answers).length);

  onMount(() => {
    loadQuestions();
  });

  async function loadQuestions() {
    loading = true;
    error = '';

    try {
      const response = await fetch(API_URL);

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();
      console.log(data)
      if (!Array.isArray(data.questions)) {
        throw new Error('The server returned an invalid record list.');
      }

      for (const question of data.questions) {
        if (
          question.id === undefined ||
          typeof question.term !== 'string' ||
          typeof question.soc !== 'string' ||
          typeof question.llt_code !== 'number' ||
          !Array.isArray(question.grades) ||
          !question.grades.every(
            (grade: Grade) =>
              typeof grade.level === 'number' &&
              (
                typeof grade.description === 'string' ||
                grade.description === null
              )
          )
        ) {
          throw new Error(
            'A record has an invalid format. Check the JSON data.'
          );
        }
      }

      questions = data.results;
      currentIndex = 0;
      answers = {};
      symptoms = '';
      saved = false;
      submissionId = '';
      saveError = '';
    } catch (err) {
      error =
        err instanceof Error
          ? err.message
          : 'Could not connect to the server.';
    } finally {
      loading = false;
    }
  }

  function selectAnswer(level: number) {
    answers[currentIndex] = level;
    saveError = '';
  }

  function nextQuestion() {
    if (
      currentIndex < totalQuestions - 1 &&
      answers[currentIndex] !== undefined
    ) {
      currentIndex += 1;
      saveError = '';
    } else if (
      currentIndex === totalQuestions - 1 &&
      answers[currentIndex] !== undefined
    ) {
      // Move to the separate symptoms step.
      currentIndex = totalQuestions;
      saveError = '';
    }
  }

  function previousQuestion() {
    if (currentIndex > 0) {
      currentIndex -= 1;
      saveError = '';
    }
  }

  async function finishQuiz() {
    if (
      saving ||
      saved ||
      questions.some(
        (_, index) => answers[index] === undefined
      )
    ) {
      return;
    }

    saving = true;
    saveError = '';

    const payload: SubmissionPayload = {
      symptoms: symptoms.trim(),

      answers: questions.map((question, index) => {
        const selectedLevel = answers[index];

        const selectedGrade = question.grades.find(
          (grade) => grade.level === selectedLevel
        );

        return {
          question_id: question.id,
          llt_code: question.llt_code,
          soc: question.soc,
          term: question.term,
          definition: question.definition ?? null,
          answer: selectedLevel,
          grade_description: selectedGrade?.description ?? null
        };
      })
    };

    try {
      const response = await fetch(ANSWERS_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
      });

      if (!response.ok) {
        let message = `Server returned ${response.status}`;

        try {
          const data = await response.json();
          message = data.detail ?? message;
        } catch {
          // Keep the HTTP status message.
        }

        throw new Error(message);
      }

      const data = await response.json();

      submissionId = data.submission_id ?? '';
      saved = true;
      currentIndex = totalQuestions + 1;
    } catch (err) {
      saveError =
        err instanceof Error
          ? err.message
          : 'Could not save your answers. Please try again.';
    } finally {
      saving = false;
    }
  }

  function restartQuiz() {
    answers = {};
    symptoms = '';
    currentIndex = 0;
    saved = false;
    saveError = '';
    submissionId = '';
  }

  function getGradeDescription(
    question: Question,
    level: number
  ) {
    return (
      question.grades.find(
        (grade) => grade.level === level
      )?.description ?? 'No description available'
    );
  }
</script>

<svelte:head>
  <title>Dotazník symptomů</title>
  <meta
    name="description"

  />
</svelte:head>

<main class="page">
  <section class="card">
    <header class="header">
      <div class="logo" aria-hidden="true">M</div>

      <div>
        <h1>Dotazník symptomů</h1>
      </div>
    </header>

    {#if loading}
      <div class="message" aria-live="polite">
        <div class="spinner"></div>
        <h2>Loading records...</h2>
        <p>Please wait while we connect to the server.</p>
      </div>

    {:else if error}
      <div class="message">
        <div class="message-icon">!</div>
        <h2>Unable to load records</h2>
        <p>{error}</p>
        <p class="hint">
          Check that FastAPI is running on port 8000 and that
          your JSON records have the expected structure.
        </p>
        <button
          class="primary-button"
          onclick={loadQuestions}
        >
          Try again
        </button>
      </div>

    {:else if totalQuestions === 0}
      <div class="message">
        <div class="message-icon">i</div>
        <h2>No records found</h2>
        <p>
          Add medical terminology records to your backend JSON
          files and ensure their IDs are enabled.
        </p>
        <button
          class="secondary-button"
          onclick={loadQuestions}
        >
          Reload records
        </button>
      </div>

    {:else if finished}
      <div class="results">
        <div class="success-icon">✓</div>

        <p class="eyebrow">QUESTIONNAIRE COMPLETE</p>
        <h2>Thank you!</h2>

        {#if saved}
          <p class="save-success" role="status">
            Your symptoms and answers have been saved successfully.
          </p>

          {#if submissionId}
            <p class="submission-id">
              Submission ID: {submissionId}
            </p>
          {/if}
        {/if}

        <p class="results-description">
          You have completed all {totalQuestions} medical questions.
        </p>

        <div class="summary">
          <div class="summary-number">
            {answeredCount}
          </div>
          <div>
            <strong>Records answered</strong>
            <p>Review your selected grades below.</p>
          </div>
        </div>

        <section class="submitted-symptoms">
          <h3>Patient symptoms</h3>

          {#if symptoms.trim()}
            <p>{symptoms}</p>
          {:else}
            <p class="muted">No symptoms provided.</p>
          {/if}
        </section>

        <div class="answer-list">
          {#each questions as question, i (question.id)}
            <article class="answer-item">
              <p class="answer-number">
                RECORD {i + 1}
              </p>

              <h3>{question.term}</h3>

              <p class="answer-soc">
                {question.soc}
              </p>

              <p class="term-code">
                LLT code: {question.llt_code}
              </p>

              {#if question.definition}
                <p class="answer-description">
                  {question.definition}
                </p>
              {/if}

              {#if answers[i] !== undefined}
                <div class="selected-grade">
                  <span class="grade-number">
                    {answers[i]}
                  </span>

                  <div>
                    <strong>Grade {answers[i]}</strong>
                    <p>
                      {getGradeDescription(question, answers[i])}
                    </p>
                  </div>
                </div>
              {:else}
                <p class="unanswered">Not answered</p>
              {/if}
            </article>
          {/each}
        </div>

        <button
          class="primary-button full-width"
          onclick={restartQuiz}
        >
          Start again
        </button>
      </div>

    {:else if symptomsStep}
      <div class="quiz">
        <div class="final-step-heading">
          <div class="step-number">✓</div>
          <div>
            <p class="eyebrow">Poslední krok</p>
            <h2 class="question-title">Dodatečné symptomy</h2>
          </div>
        </div>

        <p class="final-step-description">
          Všechny otázky vyplněny. Pokud máš problém, který nebyl zmíněn vyplň ho níže.
  
        </p>

        <div class="final-step-progress">
          <div class="progress-info">
            <span>Poslední krok</span>
            <span>Připraveno k odeslání</span>
          </div>

          <div class="progress-track">
            <div class="progress-fill" style="width: 100%"></div>
          </div>
        </div>

        <section
          class="symptoms-card"
          aria-labelledby="symptoms-heading"
        >
          <label id="symptoms-heading" for="patient-symptoms">
            <span class="optional-label">Optional</span>
          </label>

          <p class="symptoms-hint">
            Napiš symptomy, kdy začaly a frekvenci.
          </p>

          <textarea
            id="patient-symptoms"
            class="symptoms-input"
            bind:value={symptoms}
            maxlength="5000"
            rows="7"
            placeholder="Vyplň dodatečné problémy"
            aria-describedby="symptoms-counter"
          ></textarea>

          <div
            id="symptoms-counter"
            class="symptoms-counter"
          >
            {symptoms.length}/5000 znaků
          </div>
        </section>

        <div class="navigation">
          <button
            class="secondary-button"
            onclick={previousQuestion}
            disabled={saving}
          >
            ← Zpět
          </button>

          <button
            class="primary-button"
            onclick={finishQuiz}
            disabled={saving || saved}
          >
            {saving ? 'Submitting...' : 'Odeslat dotazník →'}
          </button>
        </div>

        {#if saveError}
          <p class="save-error" role="alert">
            Nepodařilo se uložit: {saveError}
          </p>
          <p class="retry-note">
            Zkus odeslat znovu.
          </p>
        {/if}

        <p class="footer-note">
        
        </p>
      </div>

    {:else}
      <div class="quiz">
        <div class="progress-info">
          <span>
            Otázka {currentIndex + 1} z {totalQuestions}
          </span>
          <span>{Math.round(progress)}% Hotovo</span>
        </div>

        <div
          class="progress-track"
          role="progressbar"
          aria-label="Questionnaire progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
        >
          <div
            class="progress-fill"
            style={`width: ${progress}%`}
          ></div>
        </div>

        <div class="term-meta">
          <span class="soc-badge">
            {currentQuestion.soc}
          </span>
        </div>

        <h2 class="question-title">
          {currentQuestion.term}
        </h2>

        {#if currentQuestion.definition}
          <div class="definition-card">
            <p class="section-label">Popis</p>
            <p class="definition">
              {currentQuestion.definition}
            </p>
          </div>
        {/if}

        {#if currentQuestion.navigational_note}
          <div class="note">
            <strong>Navigational note:</strong>
            {currentQuestion.navigational_note}
          </div>
        {/if}


        <div class="grade-heading">
          <div>
            <h3> Vyber odpověď z možností. Dodatečné problémy můžeš dopsat v posledním kroku</h3>
          </div>
        </div>

        <div class="options grade-options">
          {#each currentQuestion.grades as grade (grade.level)}
            <button
              class="option grade-option"
              class:selected={
                answers[currentIndex] === grade.level
              }
              aria-pressed={
                answers[currentIndex] === grade.level
              }
              onclick={() => selectAnswer(grade.level)}
            >
              <span class="grade-number">
                {grade.level}
              </span>

              <span class="grade-content">
                <span class="grade-title">
                  Úroveň {grade.level}
                </span>
                <span class="grade-description">
                  {grade.description ??
                    'No description available'}
                </span>
              </span>

              <span class="option-check">
                {answers[currentIndex] === grade.level
                  ? '✓'
                  : ''}
              </span>
            </button>
          {/each}
        </div>

        <div class="navigation">
          <button
            class="secondary-button"
            onclick={previousQuestion}
            disabled={currentIndex === 0 || saving}
          >
            ← Zpět
          </button>

          <button
            class="primary-button"
            onclick={nextQuestion}
            disabled={
              answers[currentIndex] === undefined ||
              saving
            }
          >
            {currentIndex === totalQuestions - 1
              ? 'Další: Další symptomy →'
              : 'Další otázka →'}
          </button>
        </div>

        {#if saveError}
          <p class="save-error" role="alert">
            {saveError}
          </p>
        {/if}
      </div>
    {/if}
  </section>
</main>

<style>
  :global(*) {
    box-sizing: border-box;
  }

  :global(body) {
    margin: 0;
    font-family:
      Inter, ui-sans-serif, system-ui, -apple-system,
      BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #192238;
    background: #f3f6fc;
  }

  :global(button),
  :global(textarea) {
    font: inherit;
  }

  .page {
    min-height: 100vh;
    padding: 48px 20px 24px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    background:
      radial-gradient(
        ellipse at top left,
        #e2eaff 0,
        transparent 45%
      ),
      #f3f6fc;
  }

  .card {
    width: 100%;
    max-width: 760px;
    padding: 36px;
    background: white;
    border: 1px solid #e6eaf2;
    border-radius: 22px;
    box-shadow: 0 18px 55px rgb(34 54 94 / 8%);
  }

  .header {
    display: flex;
    align-items: center;
    gap: 14px;
    padding-bottom: 26px;
    border-bottom: 1px solid #edf0f6;
  }

  .logo {
    width: 48px;
    height: 48px;
    display: grid;
    place-items: center;
    flex-shrink: 0;
    border-radius: 15px;
    background: #e9edff;
    color: #4658d9;
    font-size: 25px;
    font-weight: 800;
  }

  h1 {
    margin: 0;
    font-size: 22px;
    letter-spacing: -0.6px;
  }

  .subtitle {
    margin: 5px 0 0;
    color: #778198;
    font-size: 14px;
    line-height: 1.6;
  }

  .quiz {
    padding-top: 28px;
  }

  .eyebrow {
    margin: 0 0 12px;
    color: #5965d9;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.5px;
  }

  .progress-info {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    color: #6f7890;
    font-size: 13px;
    font-weight: 600;
  }

  .progress-track {
    height: 7px;
    margin: 12px 0 34px;
    overflow: hidden;
    border-radius: 20px;
    background: #edf0f8;
  }

  .progress-fill {
    height: 100%;
    border-radius: inherit;
    background: linear-gradient(90deg, #5965e8, #8291ff);
    transition: width 200ms ease;
  }

  .term-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 9px;
    margin-bottom: 18px;
  }

  .soc-badge {
    padding: 6px 10px;
    border-radius: 8px;
    background: #eef0ff;
    color: #4e5ad4;
    font-size: 12px;
    font-weight: 700;
    line-height: 1.5;
  }

  .term-code {
    color: #798297;
    font-size: 12px;
    overflow-wrap: anywhere;
  }

  .question-title {
    margin: 0;
    font-size: clamp(25px, 4vw, 34px);
    line-height: 1.3;
    letter-spacing: -0.8px;
    overflow-wrap: anywhere;
  }

  .definition-card {
    margin-top: 20px;
    padding: 18px;
    border: 1px solid #e5e8f3;
    border-radius: 13px;
    background: #fafbff;
  }

  .section-label {
    margin: 0 0 8px;
    color: #737d94;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.2px;
  }

  .definition {
    margin: 0;
    color: #515d74;
    font-size: 14px;
    line-height: 1.8;
    overflow-wrap: anywhere;
  }

  .note {
    margin: 14px 0;
    padding: 12px 14px;
    border-left: 3px solid #8291ff;
    border-radius: 5px;
    background: #f5f6ff;
    color: #46516a;
    font-size: 13px;
    line-height: 1.6;
  }

  .change-label {
    margin: 12px 0;
    color: #798297;
    font-size: 12px;
  }

  .grade-heading {
    margin-top: 28px;
  }

  .grade-heading h3 {
    margin: 0;
    font-size: 17px;
  }

  .instruction {
    margin: 7px 0 17px;
    color: #7b8498;
    font-size: 13px;
    line-height: 1.6;
  }

  .options {
    display: grid;
    gap: 11px;
  }

  .grade-options {
    grid-template-columns: 1fr;
  }

  .option {
    width: 100%;
    min-width: 0;
    min-height: 76px;
    padding: 14px;
    display: flex;
    align-items: center;
    gap: 13px;
    text-align: left;
    color: #28334b;
    background: white;
    border: 1px solid #dfe4ef;
    border-radius: 12px;
    cursor: pointer;
    transition: border-color 150ms, background 150ms;
  }

  .option:hover {
    border-color: #8d98ef;
    background: #fafaff;
  }

  .option.selected {
    border: 1.5px solid #626ce5;
    background: #f3f4ff;
  }

  .grade-number {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 10px;
    background: #f0f2f8;
    color: #28334b;
    font-size: 18px;
    font-weight: 800;
  }

  .option.selected .grade-number {
    background: #5965dc;
    color: white;
  }

  .grade-content {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .grade-title {
    font-size: 14px;
    font-weight: 750;
  }

  .grade-description {
    color: #798297;
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .option-check {
    width: 20px;
    flex-shrink: 0;
    color: #5965d9;
    font-weight: 800;
    text-align: center;
  }

  .navigation {
    margin-top: 28px;
    padding-top: 22px;
    display: flex;
    justify-content: space-between;
    gap: 12px;
    border-top: 1px solid #edf0f6;
  }

  .primary-button,
  .secondary-button {
    min-height: 46px;
    padding: 0 20px;
    border-radius: 11px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: background 150ms, transform 150ms;
  }

  .primary-button {
    color: white;
    background: #5965dc;
    border: 1px solid #5965dc;
  }

  .primary-button:hover:not(:disabled) {
    background: #4753c9;
    transform: translateY(-1px);
  }

  .secondary-button {
    color: #46516a;
    background: white;
    border: 1px solid #dfe4ef;
  }

  .secondary-button:hover:not(:disabled) {
    background: #f6f7fb;
  }

  button:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  button:focus-visible {
    outline: 3px solid #a8b1ff;
    outline-offset: 3px;
  }

  .final-step-heading {
    display: flex;
    align-items: center;
    gap: 15px;
  }

  .final-step-heading .eyebrow {
    margin-bottom: 8px;
  }

  .final-step-heading .question-title {
    font-size: 28px;
  }

  .step-number {
    width: 48px;
    height: 48px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 14px;
    color: #16794a;
    background: #e5f8ed;
    font-size: 22px;
    font-weight: 800;
  }

  .final-step-description {
    margin: 20px 0;
    color: #68738a;
    font-size: 14px;
    line-height: 1.8;
  }

  .final-step-progress {
    margin-bottom: 28px;
  }

  .final-step-progress .progress-track {
    margin-bottom: 0;
  }

  .symptoms-card {
    padding: 20px;
    border: 1px solid #e5e8f3;
    border-radius: 13px;
    background: #fafbff;
  }

  .symptoms-card label {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 9px;
    color: #28334b;
    font-size: 15px;
    font-weight: 750;
  }

  .optional-label {
    padding: 4px 8px;
    border-radius: 6px;
    background: #eef0ff;
    color: #5965dc;
    font-size: 11px;
    font-weight: 600;
  }

  .symptoms-hint {
    margin: 8px 0 14px;
    color: #798297;
    font-size: 13px;
    line-height: 1.7;
  }

  .symptoms-input {
    width: 100%;
    min-height: 150px;
    padding: 13px;
    resize: vertical;
    border: 1px solid #dfe4ef;
    border-radius: 10px;
    background: white;
    color: #192238;
    font-size: 14px;
    line-height: 1.7;
  }

  .symptoms-input:focus {
    outline: 3px solid #a8b1ff;
    outline-offset: 2px;
    border-color: #5965dc;
  }

  .symptoms-counter {
    margin-top: 7px;
    color: #9299aa;
    font-size: 11px;
    text-align: right;
  }

  .submission-reminder {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    margin-top: 18px;
    padding: 13px;
    border-radius: 10px;
    background: #f5f6ff;
  }

  .reminder-icon {
    width: 22px;
    height: 22px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: #e3e7ff;
    color: #5965dc;
    font-size: 12px;
    font-weight: 800;
  }

  .submission-reminder p {
    margin: 0;
    color: #59647b;
    font-size: 12px;
    line-height: 1.7;
  }

  .footer-note {
    margin: 18px 0 0;
    color: #9299aa;
    font-size: 12px;
    line-height: 1.6;
    text-align: center;
  }

  .message,
  .results {
    padding: 36px 0 12px;
    text-align: center;
  }

  .message h2,
  .results h2 {
    margin: 14px 0 8px;
    font-size: 25px;
    letter-spacing: -0.5px;
  }

  .message p,
  .results-description {
    color: #798297;
    font-size: 14px;
    line-height: 1.7;
  }

  .message-icon,
  .success-icon {
    width: 54px;
    height: 54px;
    margin: auto;
    display: grid;
    place-items: center;
    border-radius: 50%;
    color: #5965dc;
    background: #eef0ff;
    font-size: 25px;
    font-weight: 800;
  }

  .success-icon {
    color: #16794a;
    background: #e5f8ed;
  }

  .hint {
    font-size: 12px !important;
  }

  .spinner {
    width: 34px;
    height: 34px;
    margin: auto;
    border: 3px solid #e5e8f5;
    border-top-color: #5965dc;
    border-radius: 50%;
    animation: spin 800ms linear infinite;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .summary {
    margin: 26px 0;
    padding: 18px;
    display: flex;
    align-items: center;
    gap: 16px;
    text-align: left;
    border: 1px solid #e5e8f3;
    border-radius: 13px;
    background: #fafbff;
  }

  .summary-number {
    width: 48px;
    height: 48px;
    display: grid;
    place-items: center;
    border-radius: 12px;
    background: #e9edff;
    color: #4e5ad4;
    font-size: 22px;
    font-weight: 800;
  }

  .summary p {
    margin: 5px 0 0;
    color: #798297;
    font-size: 13px;
  }

  .submitted-symptoms {
    margin: 0 0 24px;
    padding: 18px;
    text-align: left;
    border: 1px solid #e5e8f3;
    border-radius: 13px;
    background: #fafbff;
  }

  .submitted-symptoms h3 {
    margin: 0 0 8px;
    font-size: 15px;
  }

  .submitted-symptoms p {
    margin: 0;
    color: #515d74;
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .submitted-symptoms .muted {
    color: #9299aa;
  }

  .answer-list {
    margin-bottom: 24px;
    text-align: left;
  }

  .answer-item {
    padding: 19px 0;
    border-bottom: 1px solid #edf0f6;
  }

  .answer-number {
    margin: 0 0 7px;
    color: #737d94;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
  }

  .answer-item h3 {
    margin: 0;
    font-size: 16px;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }

  .answer-soc {
    margin: 5px 0;
    color: #798297;
    font-size: 12px;
  }

  .answer-description {
    color: #515d74;
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .selected-grade {
    margin-top: 14px;
    padding: 12px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
    border-radius: 10px;
    background: #f5f6ff;
  }

  .selected-grade .grade-number {
    background: #5965dc;
    color: white;
  }

  .selected-grade strong {
    font-size: 13px;
  }

  .selected-grade p {
    margin: 5px 0 0;
    color: #515d74;
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .unanswered {
    color: #b42318;
    font-size: 13px;
  }

  .save-success {
    padding: 12px;
    border-radius: 10px;
    color: #16794a !important;
    background: #e5f8ed;
    font-size: 14px;
  }

  .submission-id {
    color: #798297;
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .save-error {
    color: #b42318;
    font-size: 13px;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }

  .retry-note {
    color: #798297;
    font-size: 12px;
  }

  .full-width {
    width: 100%;
  }

  .page-footer {
    margin: 20px 0 0;
    color: #8a93a7;
    font-size: 12px;
    text-align: center;
  }

  @media (max-width: 520px) {
    .page {
      padding: 18px 12px;
      justify-content: flex-start;
    }

    .card {
      padding: 22px 18px;
      border-radius: 17px;
    }

    .header {
      padding-bottom: 20px;
    }

    .header h1 {
      font-size: 19px;
    }

    .navigation {
      align-items: stretch;
      flex-wrap: wrap;
    }

    .primary-button,
    .secondary-button {
      padding: 0 13px;
    }

    .grade-option {
      padding: 12px 10px;
      gap: 10px;
    }

    .grade-description {
      font-size: 12px;
    }

    .final-step-heading .question-title {
      font-size: 24px;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
</style>
