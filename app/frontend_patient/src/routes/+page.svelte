
<script lang="ts">
  import { onMount } from 'svelte';
  import { buildSteps, visibleSteps, effectiveAnswers, type Step } from '#lib/quiz.js';

  type Grade = {
    level: number;
    description: string | null;
  };

  type Question = {
    id: string | number;
    llt_code: number;
    soc: string;
    soc_question?: string | null;
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
  const groupChoices = [
    { value: false, label: 'Ne', title: 'Nemám potíže', description: 'Tyto otázky se automaticky vyplní jako bez potíží.' },
    { value: true, label: 'Ano', title: 'Mám potíže', description: 'Zobrazí se jednotlivé otázky.' }
  ];

  let questions = $state<Question[]>([]);
  let individualAnswers = $state<Record<number, number>>({});
  let symptoms = $state('');

  let quizSteps = $state<Step[]>([]);
  let stepIndex = $state(0);

  let groupAnswers = $state<Record<string, boolean>>({});
  let steps = $derived(visibleSteps(quizSteps, groupAnswers));
  let answers = $derived(effectiveAnswers(quizSteps, individualAnswers, groupAnswers));

  let loading = $state(true);
  let saving = $state(false);
  let error = $state('');
  let saveError = $state('');
  let saved = $state(false);
  let submissionId = $state('');

  let currentStep = $derived(steps[stepIndex]);

  let currentGroup = $derived(
    currentStep?.kind === 'group' ? currentStep.group : undefined
  );

  let currentQuestionIndex = $derived(
    currentStep?.kind === 'question' ? currentStep.questionIndex : -1
  );

  let currentQuestion = $derived(
    currentQuestionIndex >= 0 ? questions[currentQuestionIndex] : undefined
  );
  let questionTitle = $derived(
    currentGroup?.group_question ?? currentQuestion?.definition ?? 'Popis příznaku není k dispozici'
  );

  let totalQuestions = $derived(questions.length);
  let answeredCount = $derived(Object.keys(answers).length);

  // Progress reflects answered questions. Questions hidden behind a
  // group answered with "no" count as answered (grade 0).
  let progress = $derived(
    totalQuestions > 0 ? (answeredCount / totalQuestions) * 100 : 0
  );

  let symptomsStep = $derived(stepIndex === steps.length);
  let isLastStep = $derived(stepIndex === steps.length - 1);
  let canAdvance = $derived(
    currentGroup
      ? groupAnswers[currentGroup.group] !== undefined
      : answers[currentQuestionIndex] !== undefined
  );

  onMount(() => {
    loadQuestions();
  });

  async function loadQuestions() {
    loading = true;
    error = '';

    try {
      const response = await fetch(API_URL);
      if (!response.ok) throw new Error();
      const data = await response.json();
      questions = data.results;
      quizSteps = buildSteps(questions);
      restartQuiz();
    } catch {
      error = 'Dotazník se nepodařilo načíst. Zkus to prosím znovu.';
    } finally {
      loading = false;
    }
  }

  function navigate(offset: number) {
    stepIndex += offset;
    saveError = '';
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

      if (!response.ok) throw new Error();

      const data = await response.json();

      submissionId = data.submission_id;
      saved = true;
    } catch {
      saveError = 'Odpovědi se nepodařilo uložit. Zkus je prosím odeslat znovu.';
    } finally {
      saving = false;
    }
  }

  function restartQuiz() {
    individualAnswers = {};
    symptoms = '';
    stepIndex = 0;
    groupAnswers = {};
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
      )?.description ?? 'Popis není k dispozici'
    );
  }
</script>

<svelte:head>
  <title>Dotazník symptomů</title>
  <meta
    name="description"
    content="Dotazník pro sledování příznaků a nežádoucích účinků léčby."
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
        <h2>Načítání dotazníku…</h2>
        <p>Počkej prosím, než se dotazník načte.</p>
      </div>

    {:else if error}
      <div class="message">
        <div class="message-icon">!</div>
        <h2>Dotazník není dostupný</h2>
        <p>{error}</p>
        <button
          class="primary-button"
          onclick={loadQuestions}
        >
          Zkusit znovu
        </button>
      </div>

    {:else if totalQuestions === 0}
      <div class="message">
        <div class="message-icon">i</div>
        <h2>Žádné otázky k vyplnění</h2>
        <p>
          Momentálně pro tebe nejsou připravené žádné otázky.
        </p>
        <button
          class="secondary-button"
          onclick={loadQuestions}
        >
          Načíst znovu
        </button>
      </div>

    {:else if saved}
      <div class="results">
        <div class="success-icon">✓</div>

        <p class="eyebrow">DOTAZNÍK DOKONČEN</p>
        <h2>Díky za vyplnění!</h2>

        <p class="save-success" role="status">
          Tvé symptomy a odpovědi byly úspěšně uloženy.
        </p>

        {#if submissionId}
          <p class="submission-id">
            ID odeslaného dotazníku: {submissionId}
          </p>
        {/if}

        <p class="results-description">
          Všechny otázky jsou vyplněny. Celkový počet: {totalQuestions}.
        </p>

        <div class="summary">
          <div class="summary-number">
            {answeredCount}
          </div>
          <div>
            <strong>Zodpovězené otázky</strong>
            <p>Níže najdeš přehled svých odpovědí.</p>
          </div>
        </div>

        <section class="submitted-symptoms">
          <h3>Dodatečné symptomy</h3>

          {#if symptoms.trim()}
            <p>{symptoms}</p>
          {:else}
            <p class="muted">Žádné dodatečné symptomy nebyly uvedeny.</p>
          {/if}
        </section>

        <div class="answer-list">
          {#each questions as question, i (question.id)}
            <article class="answer-item">
              <p class="answer-number">
                OTÁZKA {i + 1}
              </p>

              <h3>{question.definition ?? 'Popis příznaku není k dispozici'}</h3>

              {#if question.soc_question}
                <p class="answer-soc">{question.soc_question}</p>
              {/if}

              <div class="selected-grade">
                <span class="grade-number">
                  {answers[i]}
                </span>

                <div>
                  <strong>{getGradeDescription(question, answers[i])}</strong>
                </div>
              </div>
            </article>
          {/each}
        </div>

        <button
          class="primary-button full-width"
          onclick={restartQuiz}
        >
          Vyplnit znovu
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
          Všechny otázky jsou vyplněny. Pokud máš problém, který nebyl zmíněn, vyplň ho níže.
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
            <span class="optional-label">Nepovinné</span>
          </label>

          <p class="symptoms-hint">
            Napiš symptomy, kdy začaly a frekvenci.
          </p>

          <textarea
            id="patient-symptoms"
            class="symptoms-input"
            bind:value={symptoms}
            disabled={saving}
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
            onclick={() => navigate(-1)}
            disabled={saving}
          >
            ← Zpět
          </button>

          <button
            class="primary-button"
            onclick={finishQuiz}
            disabled={saving}
          >
            {saving ? 'Odesílání…' : 'Odeslat dotazník →'}
          </button>
        </div>

        {#if saveError}
          <p class="save-error" role="alert">
            {saveError}
          </p>
        {/if}
      </div>

    {:else if currentGroup || currentQuestion}
      <div class="quiz">
        <div class="progress-info">
          <span>
            {#if currentGroup}
              Skupinová otázka
            {:else}
              Otázka {stepIndex + 1} z {steps.length}
            {/if}
          </span>
          <span>{Math.round(progress)}% hotovo</span>
        </div>

        <div
          class="progress-track"
          role="progressbar"
          aria-label="Průběh vyplňování dotazníku"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
        >
          <div
            class="progress-fill"
            style={`width: ${progress}%`}
          ></div>
        </div>

        <h2 class="question-title">
          {questionTitle}
        </h2>

        {#if currentQuestion?.navigational_note}
          <div class="note">
            <strong>Pokyny k vyplnění:</strong>
            {currentQuestion.navigational_note}
          </div>
        {/if}

        <div class="grade-heading">
          <div>
            <h3>{currentGroup ? 'Odpověz ano nebo ne.' : 'Vyber odpověď z možností.'}</h3>
          </div>
        </div>

        <div class="options grade-options">
          {#if currentGroup}
            {@const group = currentGroup}
            {#each groupChoices as choice (choice.label)}
              {@const selected = groupAnswers[group.group] === choice.value}
              <button
                class="option grade-option yes-no-option"
                class:selected
                aria-pressed={selected}
                onclick={() => groupAnswers[group.group] = choice.value}
              >
                <span class="grade-number">{choice.label}</span>
                <span class="grade-content">
                  <span class="grade-title">{choice.title}</span>
                  <span class="grade-description">{choice.description}</span>
                </span>
                <span class="option-check">{selected ? '✓' : ''}</span>
              </button>
            {/each}
          {:else if currentQuestion}
            {#each currentQuestion.grades as grade (grade.level)}
              {@const selected = answers[currentQuestionIndex] === grade.level}
              <button
                class="option grade-option"
                class:selected
                aria-pressed={selected}
                onclick={() => individualAnswers[currentQuestionIndex] = grade.level}
              >
                <span class="grade-number">
                  {grade.level}
                </span>

                <span class="grade-content">
                  <span class="grade-title">
                    {grade.description ?? 'Popis není k dispozici'}
                  </span>
                </span>

                <span class="option-check">
                  {selected ? '✓' : ''}
                </span>
              </button>
            {/each}
          {/if}
        </div>

        <div class="navigation">
          <button
            class="secondary-button"
            onclick={() => navigate(-1)}
            disabled={stepIndex === 0 || saving}
          >
            ← Zpět
          </button>

          <button
            class="primary-button"
            onclick={() => navigate(1)}
            disabled={!canAdvance}
          >
            {isLastStep
              ? 'Další: Dodatečné symptomy →'
              : 'Další otázka →'}
          </button>
        </div>
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

  .question-title {
    margin: 0;
    font-size: clamp(25px, 4vw, 34px);
    line-height: 1.3;
    letter-spacing: -0.8px;
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

  .grade-heading {
    margin-top: 28px;
  }

  .grade-heading h3 {
    margin: 0;
    font-size: 17px;
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

  .yes-no-option .grade-number {
    width: auto;
    min-width: 40px;
    padding: 0 10px;
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

  .full-width {
    width: 100%;
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
