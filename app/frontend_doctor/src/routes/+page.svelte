<script lang="ts">
  import { onMount } from 'svelte';

  type SavedAnswer = {
    question_id: string | number;
    llt_code: number;
    soc: string;
    term: string;
    definition: string | null;
    answer: number;
    grade_description: string | null;
  };

  type Submission = {
    submission_id: string;
    submitted_at: string;
    symptoms?: string | null;
    answers: SavedAnswer[];
  };

  const API_URL = 'http://127.0.0.1:8000/api/submissions';

  let submissions = $state<Submission[]>([]);
  let loading = $state(true);
  let error = $state('');
  let search = $state('');
  let expanded = $state<string | null>(null);
  let showLowerGrades = $state(false);

  let filteredSubmissions = $derived.by(() => {
    const query = search.trim().toLowerCase();

    if (!query) return submissions;

    return submissions.filter((submission) => {
      const submissionText = [
        submission.submission_id,
        submission.submitted_at,
        submission.symptoms ?? '',
        ...submission.answers.flatMap((answer) => [
          answer.question_id,
          answer.llt_code,
          answer.soc,
          answer.term,
          answer.definition ?? '',
          answer.answer,
          answer.grade_description ?? ''
        ])
      ]
        .join(' ')
        .toLowerCase();

      return submissionText.includes(query);
    });
  });

  let lowerGradeCount = $derived(
    submissions.reduce(
      (total, submission) =>
        total +
        submission.answers.filter((answer) => answer.answer < 2).length,
      0
    )
  );

  let totalAnswers = $derived(
    submissions.reduce(
      (total, submission) => total + submission.answers.length,
      0
    )
  );

  let noSymptomsCount = $derived(
    submissions.reduce(
      (total, submission) =>
        total +
        submission.answers.filter((answer) => answer.answer === 0).length,
      0
    )
  );

  onMount(() => {
    loadSubmissions();
  });

  async function loadSubmissions() {
    loading = true;
    error = '';

    try {
      const response = await fetch(API_URL);

      if (!response.ok) {
        throw new Error(`Server returned ${response.status}`);
      }

      const data = await response.json();

      if (!Array.isArray(data.results)) {
        throw new Error('The server returned an invalid submission list.');
      }

      submissions = data.results;
    } catch (err) {
      error =
        err instanceof Error
          ? err.message
          : 'Could not load submissions.';
    } finally {
      loading = false;
    }
  }

  function formatDate(value: string) {
    const date = new Date(value);

    if (Number.isNaN(date.getTime())) return value;

    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short'
    }).format(date);
  }

  function gradeLabel(level: number) {
    return level === 0
      ? 'Grade 0 — Bez symptomů'
      : `Grade ${level}`;
  }

  function toggleSubmission(id: string) {
    expanded = expanded === id ? null : id;
  }

  function maxGrade(answers: SavedAnswer[]) {
    return answers.reduce<number | null>(
      (max, answer) => Math.max(max ?? answer.answer, answer.answer),
      null
    );
  }

  function visibleAnswers(answers: SavedAnswer[]) {
    return answers
      .filter((answer) => showLowerGrades || answer.answer >= 2)
      .sort((a, b) => b.answer - a.answer);
  }
</script>

<main class="page">
  <header class="topbar">
    <div class="brand">
      <div>
      </div>
    </div>

    <button
      class="refresh-button"
      onclick={loadSubmissions}
      disabled={loading}
    >
      ↻ Obnov výsledky
    </button>
  </header>

  <section class="intro">
    <h2>Dotazník výsledky</h2>
  </section>

  

  <section class="records">
    <div class="records-heading">
      <div>
        <h2>Jednotlivé dotazníky</h2>
        <p>Klikni na dotazník pro zobrazení detailů.</p>
      </div>

      <div class="heading-actions">
      

        {#if lowerGradeCount > 0}
          <button
            class="lower-grades-button"
            aria-pressed={showLowerGrades}
            onclick={() => (showLowerGrades = !showLowerGrades)}
          >
            {showLowerGrades
              ? 'Schovej neakutní úrovně'
              : `Zobraz neakutní úrovně`}
          </button>
        {/if}
      </div>
    </div>

    {#if loading}
      <div class="state-message">
        <div class="spinner"></div>
        <p>Načítám výsledky...</p>
      </div>
    {:else if error}
      <div class="state-message error-state" role="alert">
        <h3>Nešlo načíst výsledky</h3>
        <p>{error}</p>
        <button class="primary-button" onclick={loadSubmissions}>
          Try again
        </button>
      </div>
    {:else if filteredSubmissions.length === 0}
      <div class="state-message">
        <div class="empty-icon">⌕</div>
        <h3>
          {submissions.length === 0
            ? 'No submissions yet'
            : 'No matching submissions'}
        </h3>
        <p>
          {submissions.length === 0
            ? 'Submitted questionnaires will appear here.'
            : 'Try a different search term.'}
        </p>
      </div>
    {:else}
      <div class="submission-list">
        {#each filteredSubmissions as submission (submission.submission_id)}
          {@const highestGrade = maxGrade(submission.answers)}
          <article class="submission">
            <button
              class="submission-header grade-colors"
              data-grade={highestGrade}
              aria-expanded={expanded === submission.submission_id}
              onclick={() => toggleSubmission(submission.submission_id)}
            >
              

              <div class="submission-main">
                <strong>Výsledky dotazníku</strong>
                <span>{formatDate(submission.submitted_at)}</span>
                <span class="submission-id">
                  ID: {submission.submission_id}
                </span>
                {#if submission.symptoms?.trim()}
                  <span class="symptoms-indicator">
                    Pacient dopsal nějaké symptomy ručně
                  </span>
                {/if}
              </div>

              <div class="submission-meta">
                {#if highestGrade !== null}
                  <span class="grade-badge">Nejvyšší úroveň: {highestGrade}</span>
                {/if}
                <span class="chevron" aria-hidden="true">
                  {expanded === submission.submission_id ? '−' : '+'}
                </span>
              </div>
            </button>

            {#if expanded === submission.submission_id}
              <div class="submission-details">
                {#if submission.symptoms?.trim()}
                  <section class="symptoms-card">
                    <div class="symptoms-heading">
                      <div>
                        <h3>Ručně dopsané symptomy pacientem</h3>
                      </div>
                    </div>

                    <p class="symptoms-text">{submission.symptoms}</p>
                  </section>
                {/if}

                {#if visibleAnswers(submission.answers).length === 0}
                  <div class="no-answers">
                    {#if submission.answers.length === 0}
                      Nebyly poskytnuty odpovědi.
                    {:else}
                      Nebyly nalezeny potíže co by byly nalehavé. Pro zobrazení odpovědí stiskni "Všechny odpovědi"
                    {/if}
                  </div>
                {:else}
                  {#each visibleAnswers(submission.answers) as answer, index (`${submission.submission_id}-${answer.question_id}`)}
                    <section class="answer-card grade-colors" data-grade={answer.answer}>
                      <div class="answer-top">
                        <span
                          class="grade-badge"
                        >
                          {gradeLabel(answer.answer)}
                        </span>
                      </div>

                      <h3>{answer.term} / <i>{answer.definition}</i></h3>

                      <div class="metadata">
                        <span><b>SOC</b> {answer.soc}</span>
                        <span><b>LLT code</b> {answer.llt_code}</span>
                        <span><b>Question ID</b> {answer.question_id}</span>
                      </div>

                      <div class="selected-answer">
                        <div class="grade-number">{answer.answer}</div>
                        <div>
                          <strong>{gradeLabel(answer.answer)}</strong>
                          <br>
                          <strong>{answer.grade_description ??
                            'Systém nechápe jak je v tomto stavu pacient schopný odeslat dotazník.'}</strong>
                        </div>
                      </div>
                    </section>
                  {/each}
                {/if}
              </div>
            {/if}
          </article>
        {/each}
      </div>
    {/if}
  </section>

  <footer>
    Medical Questionnaire · Doctor view
  </footer>
</main>

<style>
  :global(*) {
    box-sizing: border-box;
  }

  :global(body) {
    margin: 0;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system,
      BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #1d2939;
    background: #f5f7fb;
  }

  :global(button),
  :global(input) {
    font: inherit;
  }

  .page {
    max-width: 1120px;
    margin: 0 auto;
    padding: 32px 24px 24px;
  }

  .topbar,
  .brand,
  .records-heading,
  .submission-header,
  .answer-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .brand {
    justify-content: flex-start;
  }

  h1 {
    margin: 0;
    font-size: 23px;
    letter-spacing: -0.5px;
  }

  .intro {
    margin: 40px 0 24px;
  }

  .intro h2 {
    margin: 0;
    font-size: 29px;
    letter-spacing: -0.8px;
  }

  .intro > p {
    margin: 9px 0 0;
    color: #667085;
    font-size: 14px;
    line-height: 1.7;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 34px;
  }

  .stat {
    padding: 20px;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 8px;
    background: white;
    border: 1px solid #e4e8f0;
    border-radius: 14px;
  }

  .stat-label {
    color: #667085;
    font-size: 13px;
    font-weight: 600;
  }

  .stat strong {
    font-size: 30px;
    letter-spacing: -1px;
  }

  .stat-foot {
    color: #98a2b3;
    font-size: 12px;
  }

  .refresh-button,
  .primary-button {
    min-height: 40px;
    padding: 0 15px;
    border: 1px solid #d9dfeb;
    border-radius: 9px;
    background: white;
    color: #344054;
    font-weight: 650;
    cursor: pointer;
  }

  .primary-button {
    border-color: #465ac4;
    background: #465ac4;
    color: white;
  }

  button:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  button:focus-visible,
  input:focus-visible {
    outline: 3px solid #b7c5ff;
    outline-offset: 3px;
  }

  .records {
    padding: 24px;
    background: white;
    border: 1px solid #e4e8f0;
    border-radius: 16px;
  }

  .records-heading {
    margin-bottom: 20px;
  }

  .records-heading h2 {
    margin: 0;
    font-size: 18px;
  }

  .records-heading p {
    margin: 6px 0 0;
    color: #7b8496;
    font-size: 13px;
  }

  .heading-actions {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    flex-wrap: wrap;
    gap: 10px;
  }

  .count {
    padding: 6px 10px;
    border-radius: 20px;
    background: #f0f2f8;
    color: #596579;
    font-size: 12px;
    white-space: nowrap;
  }

  .lower-grades-button {
    padding: 8px 11px;
    border: 1px solid #d9dfeb;
    border-radius: 8px;
    background: white;
    color: #344054;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .lower-grades-button:hover {
    background: #f0f3fa;
  }

  .search {
    min-height: 46px;
    margin-bottom: 18px;
    padding: 0 13px;
    display: flex;
    align-items: center;
    gap: 10px;
    border: 1px solid #dfe4ed;
    border-radius: 10px;
    color: #778197;
  }

  .search > span {
    font-size: 23px;
  }

  .search input {
    width: 100%;
    min-width: 0;
    border: 0;
    outline: 0;
    background: transparent;
    color: #1d2939;
    font-size: 13px;
  }

  .submission {
    margin-top: 10px;
    overflow: hidden;
    border: 1px solid #e5e9f1;
    border-radius: 12px;
  }

  .submission-header {
    width: 100%;
    padding: 17px;
    justify-content: flex-start;
    border: 0;
    background: var(--grade-background, white);
    border-left: 5px solid var(--grade-color, #e5e9f1);
    text-align: left;
    cursor: pointer;
  }

  .submission-header:hover {
    filter: brightness(0.98);
  }

  .grade-colors[data-grade="0"] {
    --grade-background: #f0fdf4;
    --grade-highlight: #dcfce7;
    --grade-color: #166534;
  }

  .grade-colors[data-grade="1"] {
    --grade-background: #fefce8;
    --grade-highlight: #fef08a;
    --grade-color: #854d0e;
  }

  .grade-colors[data-grade="2"] {
    --grade-background: #fff7ed;
    --grade-highlight: #ffedd5;
    --grade-color: #9a3412;
  }

  .grade-colors[data-grade="3"] {
    --grade-background: #fef2f2;
    --grade-highlight: #fee2e2;
    --grade-color: #b91c1c;
  }

  .grade-colors[data-grade="4"] {
    --grade-background: #fff1f2;
    --grade-highlight: #ffe4e6;
    --grade-color: #9f1239;
  }

  .grade-colors[data-grade="5"] {
    --grade-background: #fdf2f8;
    --grade-highlight: #fce7f3;
    --grade-color: #831843;
  }

  .submission-icon {
    width: 42px;
    height: 42px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 11px;
    background: #eef1ff;
    color: #4b5ec6;
    font-size: 20px;
  }

  .submission-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .submission-main strong {
    font-size: 14px;
  }

  .submission-main span {
    color: #7b8496;
    font-size: 12px;
    overflow-wrap: anywhere;
  }

  .submission-main .submission-id {
    color: #98a2b3;
    font-size: 11px;
  }

  .submission-main .symptoms-indicator {
    width: fit-content;
    color: #3156a6;
    font-size: 11px;
    font-weight: 700;
  }

  .submission-meta {
    display: flex;
    align-items: center;
    gap: 13px;
  }

  .answer-count {
    color: #667085;
    font-size: 12px;
    white-space: nowrap;
  }

  .chevron {
    color: #667085;
    font-size: 21px;
  }

  .submission-details {
    padding: 0 16px 16px;
    border-top: 1px solid #edf0f5;
    background: #fbfcfe;
  }

  /* Additional patient-reported symptoms */
  .symptoms-card {
    margin-top: 16px;
    padding: 19px;
    border: 1px solid #d8e4f7;
    border-radius: 11px;
    background: #f4f8ff;
  }

  .symptoms-heading {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .symptoms-icon {
    width: 36px;
    height: 36px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 9px;
    background: #e2eaff;
    color: #4257bf;
    font-size: 23px;
    font-weight: 700;
  }

  .symptoms-heading h3 {
    margin: 0;
    color: #253858;
    font-size: 15px;
  }

  .symptoms-heading p {
    margin: 5px 0 0;
    color: #667085;
    font-size: 12px;
  }

  .symptoms-text {
    margin: 15px 0 0;
    color: #344054;
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .no-answers {
    margin-top: 16px;
    padding: 18px;
    border: 1px dashed #d9dfeb;
    border-radius: 10px;
    color: #667085;
    background: white;
    font-size: 13px;
    line-height: 1.6;
  }

  .answer-card {
    margin-top: 16px;
    padding: 19px;
    background: white;
    border: 1px solid #e6eaf1;
    border-left: 5px solid var(--grade-color, #e6eaf1);
    border-radius: 11px;
  }

  .answer-top {
    align-items: flex-start;
  }

  .record-number,
  .field-label {
    color: #7d8799;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1px;
  }

  .grade-badge {
    padding: 6px 9px;
    border-radius: 7px;
    background: var(--grade-highlight, #edf1ff);
    color: var(--grade-color, #4257bf);
    font-size: 11px;
    font-weight: 750;
    text-align: right;
  }

  .answer-card h3 {
    margin: 13px 0 12px;
    font-size: 18px;
    line-height: 1.5;
    overflow-wrap: anywhere;
  }

  .metadata {
    display: flex;
    flex-wrap: wrap;
    gap: 8px 18px;
    color: #667085;
    font-size: 12px;
  }

  .metadata b {
    margin-right: 4px;
    color: #475467;
  }

  .definition {
    margin-top: 17px;
    padding-top: 15px;
    border-top: 1px solid #edf0f5;
  }

  .definition p {
    margin: 7px 0 0;
    color: #667085;
    font-size: 13px;
    line-height: 1.7;
    overflow-wrap: anywhere;
  }

  .selected-answer {
    margin-top: 18px;
    padding: 13px;
    display: flex;
    align-items: flex-start;
    gap: 12px;
    border-radius: 10px;
    background: var(--grade-background, #f5f7fc);
  }

  .grade-number {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 10px;
    background: var(--grade-highlight, #e3e9ff);
    color: var(--grade-color, #4257bf);
    font-size: 18px;
    font-weight: 800;
  }

  .selected-answer strong {
    font-size: 13px;
  }

  .selected-answer p {
    margin: 5px 0 0;
    color: #667085;
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .state-message {
    padding: 44px 15px;
    text-align: center;
    color: #667085;
  }

  .state-message h3 {
    margin: 12px 0 7px;
    color: #344054;
  }

  .state-message p {
    font-size: 13px;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .empty-icon {
    font-size: 32px;
  }

  .spinner {
    width: 30px;
    height: 30px;
    margin: auto;
    border: 3px solid #e6eaf5;
    border-top-color: #5965dc;
    border-radius: 50%;
    animation: spin 800ms linear infinite;
  }

  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  footer {
    padding: 25px 0 0;
    color: #98a2b3;
    font-size: 12px;
    text-align: center;
  }

  @media (max-width: 650px) {
    .page {
      padding: 22px 13px;
    }

    .intro {
      margin-top: 30px;
    }

    .intro h2 {
      font-size: 25px;
    }

    .stats {
      grid-template-columns: 1fr;
      gap: 10px;
    }

    .stat {
      padding: 15px;
    }

    .stat strong {
      font-size: 25px;
    }

    .records {
      padding: 15px;
    }

    .records-heading {
      align-items: flex-start;
      flex-direction: column;
    }

    .heading-actions {
      justify-content: flex-start;
    }

    .submission-header {
      padding: 12px;
      gap: 10px;
    }

    .submission-icon {
      width: 35px;
      height: 35px;
    }

    .submission-meta {
      gap: 6px;
    }

    .answer-count {
      font-size: 10px;
      white-space: normal;
      text-align: right;
    }

    .submission-details {
      padding: 0 9px 10px;
    }

    .answer-card,
    .symptoms-card {
      padding: 14px;
    }

    .brand {
      gap: 10px;
    }

    h1 {
      font-size: 19px;
    }

    .refresh-button {
      padding: 0 10px;
      font-size: 12px;
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
