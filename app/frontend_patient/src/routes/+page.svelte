
<script lang="ts">
  import { onMount } from 'svelte';

  type Question = {
    id?: string | number;
    question: string;
    options: string[];
  };

  const API_URL = 'http://127.0.0.1:8000/api/questions';

  let questions = $state<Question[]>([]);
  let answers = $state<Record<number, string>>({});
  let currentIndex = $state(0);
  let loading = $state(true);
  let error = $state('');

  let currentQuestion = $derived(questions[currentIndex]);
  let totalQuestions = $derived(questions.length);
  let progress = $derived(
    totalQuestions > 0
      ? ((currentIndex + 1) / totalQuestions) * 100
      : 0
  );
  let finished = $derived(
    totalQuestions > 0 && currentIndex >= totalQuestions
  );

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

      if (!Array.isArray(data.results)) {
        throw new Error('The server returned an invalid question list.');
      }

      questions = data.results;
      currentIndex = 0;
      answers = {};
    } catch (err) {
      error =
        err instanceof Error
          ? err.message
          : 'Could not connect to the server.';
    } finally {
      loading = false;
    }
  }

  function selectAnswer(option: string) {
    answers[currentIndex] = option;
  }

  function nextQuestion() {
    if (currentIndex < totalQuestions) {
      currentIndex += 1;
    }
  }

  function previousQuestion() {
    if (currentIndex > 0) {
      currentIndex -= 1;
    }
  }

  function restartQuiz() {
    answers = {};
    currentIndex = 0;
  }

  function retryLoading() {
    loadQuestions();
  }
</script>

<svelte:head>
  <title>Questionnaire</title>
  <meta
    name="description"
    content="Complete the questionnaire and review your answers."
  />
</svelte:head>

<main class="page">
  <section class="card">
    <header class="header">
      <div class="logo">Q</div>
      <div>
        <h1>Questionnaire</h1>
        <p class="subtitle">Your answers, one step at a time.</p>
      </div>
    </header>

    {#if loading}
      <div class="message">
        <div class="spinner"></div>
        <h2>Loading questions...</h2>
        <p>Please wait while we connect to the server.</p>
      </div>

    {:else if error}
      <div class="message">
        <div class="message-icon">!</div>
        <h2>Unable to load questions</h2>
        <p>{error}</p>
        <p class="hint">
          Check that your FastAPI server is running on port 8000.
        </p>
        <button class="primary-button" onclick={retryLoading}>
          Try again
        </button>
      </div>

    {:else if totalQuestions === 0}
      <div class="message">
        <h2>No questions found</h2>
        <p>Add questions to your backend/questions/questions.json file.</p>
      </div>

    {:else if finished}
      <div class="results">
        <div class="success-icon">✓</div>
        <p class="eyebrow">QUESTIONNAIRE COMPLETE</p>
        <h2>Thank you!</h2>
        <p class="results-description">
          You have completed all {totalQuestions} questions.
        </p>

        <div class="summary">
          <div class="summary-number">
            {Object.keys(answers).length}
          </div>
          <div>
            <strong>Questions answered</strong>
            <p>Review your answers below.</p>
          </div>
        </div>

        <div class="answer-list">
          {#each questions as question, i}
            <article class="answer-item">
              <p class="answer-number">QUESTION {i + 1}</p>
              <h3>{question.question}</h3>
              <p class="answer-value">
                {answers[i] ?? 'Not answered'}
              </p>
            </article>
          {/each}
        </div>

        <button class="primary-button full-width" onclick={restartQuiz}>
          Start again
        </button>
      </div>

    {:else}
      <div class="quiz">
        <div class="progress-info">
          <span>Question {currentIndex + 1} of {totalQuestions}</span>
          <span>{Math.round(progress)}% complete</span>
        </div>

        <div
          class="progress-track"
          role="progressbar"
          aria-label="Questionnaire progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress)}
        >
          <div class="progress-fill" style={`width: ${progress}%`}></div>
        </div>

        <p class="eyebrow">YOUR QUESTION</p>
        <h2 class="question-title">{currentQuestion.question}</h2>
        <p class="instruction">Choose one answer below.</p>

        <div class="options">
          {#each currentQuestion.options as option, i}
            <button
              class="option"
              class:selected={answers[currentIndex] === option}
              aria-pressed={answers[currentIndex] === option}
              onclick={() => selectAnswer(option)}
            >
              <span class="option-letter">
                {String.fromCharCode(65 + i)}
              </span>
              <span class="option-text">{option}</span>
              <span class="option-check">
                {#if answers[currentIndex] === option}
                  ✓
                {/if}
              </span>
            </button>
          {/each}
        </div>

        <div class="navigation">
          <button
            class="secondary-button"
            onclick={previousQuestion}
            disabled={currentIndex === 0}
          >
            ← Back
          </button>

          <button
            class="primary-button"
            onclick={nextQuestion}
            disabled={!answers[currentIndex]}
          >
            {currentIndex === totalQuestions - 1
              ? 'Finish'
              : 'Next question'} →
          </button>
        </div>

        <p class="footer-note">
          Your progress is kept while this page is open.
        </p>
      </div>
    {/if}
  </section>

  <p class="page-footer">Questionnaire App · Powered by FastAPI</p>
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

  :global(button) {
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
      radial-gradient(ellipse at top left, #e2eaff 0, transparent 45%),
      #f3f6fc;
  }

  .card {
    width: 100%;
    max-width: 680px;
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
  }

  .quiz {
    padding-top: 28px;
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

  .eyebrow {
    margin: 0 0 10px;
    color: #5965d9;
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 1.5px;
  }

  .question-title {
    margin: 0;
    font-size: clamp(23px, 4vw, 30px);
    line-height: 1.3;
    letter-spacing: -0.8px;
    overflow-wrap: anywhere;
  }

  .instruction {
    margin: 10px 0 22px;
    color: #7b8498;
    font-size: 14px;
  }

  .options {
    display: grid;
    gap: 11px;
  }

  .option {
    width: 100%;
    min-height: 62px;
    padding: 12px 15px;
    display: flex;
    align-items: center;
    gap: 14px;
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

  .option-letter {
    width: 34px;
    height: 34px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border: 1px solid #e0e5ef;
    border-radius: 9px;
    color: #737d94;
    background: #f8f9fc;
    font-size: 13px;
    font-weight: 700;
  }

  .selected .option-letter {
    color: white;
    background: #626ce5;
    border-color: #626ce5;
  }

  .option-text {
    flex: 1;
    overflow-wrap: anywhere;
    font-size: 15px;
  }

  .option-check {
    width: 20px;
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

  .footer-note {
    margin: 18px 0 0;
    color: #9299aa;
    font-size: 12px;
    text-align: center;
  }

  .message,
  .results {
    padding: 46px 0 12px;
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
    to {
      transform: rotate(360deg);
    }
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

  .answer-list {
    margin-bottom: 24px;
    text-align: left;
  }

  .answer-item {
    padding: 17px 0;
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
    font-size: 15px;
    line-height: 1.5;
  }

  .answer-value {
    margin: 9px 0 0;
    color: #505fd2;
    font-size: 14px;
    font-weight: 600;
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

    .navigation {
      align-items: stretch;
    }

    .primary-button,
    .secondary-button {
      padding: 0 13px;
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
