
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
    answers: SavedAnswer[];
  };

  const API_URL = 'http://127.0.0.1:8000/api/submissions';

  let submissions = $state<Submission[]>([]);
  let loading = $state(true);
  let error = $state('');
  let search = $state('');
  let expanded = $state<string | null>(null);

  let filteredSubmissions = $derived.by(() => {
    const query = search.trim().toLowerCase();

    if (!query) return submissions;

    return submissions.filter((submission) => {
      const submissionText = [
        submission.submission_id,
        submission.submitted_at,
        ...submission.answers.flatMap((answer) => [
          answer.question_id,
          answer.llt_code,
          answer.soc,
          answer.term,
          answer.definition ?? '',
          answer.answer,
          answer.grade_description ?? ''
        ])
      ].join(' ').toLowerCase();

      return submissionText.includes(query);
    });
  });

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
      error = err instanceof Error
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
      ? 'Grade 0 — No symptoms'
      : `Grade ${level}`;
  }

  function toggleSubmission(id: string) {
    expanded = expanded === id ? null : id;
  }
</script>

<svelte:head>
  <title>Doctor Dashboard | Medical Questionnaire</title>
  <meta
    name="description"
    content="Review submitted medical questionnaire answers."
  />
</svelte:head>

<main class="page">
  <header class="topbar">
    <div class="brand">
      <div class="logo">M</div>
      <div>
        <p class="overline">CLINICAL WORKSPACE</p>
        <h1>Doctor Dashboard</h1>
      </div>
    </div>

    <button
      class="refresh-button"
      onclick={loadSubmissions}
      disabled={loading}
    >
      ↻ Refresh
    </button>
  </header>

  <section class="intro">
    <p class="overline">QUESTIONNAIRE MANAGEMENT</p>
    <h2>Submitted responses</h2>
    <p>
      Review questionnaire submissions and the grades recorded for each
      medical term.
    </p>
  </section>

  <section class="stats" aria-label="Submission statistics">
    <article class="stat">
      <span class="stat-label">Submissions</span>
      <strong>{submissions.length}</strong>
      <span class="stat-foot">Saved questionnaires</span>
    </article>

    <article class="stat">
      <span class="stat-label">Answers recorded</span>
      <strong>{totalAnswers}</strong>
      <span class="stat-foot">Across all submissions</span>
    </article>

    <article class="stat">
      <span class="stat-label">Grade 0 answers</span>
      <strong>{noSymptomsCount}</strong>
      <span class="stat-foot">Recorded as no symptoms</span>
    </article>
  </section>

  <section class="records">
    <div class="records-heading">
      <div>
        <h2>Questionnaire history</h2>
        <p>Newest submissions appear first.</p>
      </div>
      <span class="count">{filteredSubmissions.length} shown</span>
    </div>

    <label class="search">
      <span aria-hidden="true">⌕</span>
      <input
        type="search"
        bind:value={search}
        placeholder="Search term, SOC, LLT code or submission ID..."
      />
    </label>

    {#if loading}
      <div class="state-message">
        <div class="spinner"></div>
        <p>Loading saved submissions...</p>
      </div>
    {:else if error}
      <div class="state-message error-state" role="alert">
        <h3>Unable to load submissions</h3>
        <p>{error}</p>
        <button class="primary-button" onclick={loadSubmissions}>
          Try again
        </button>
      </div>
    {:else if filteredSubmissions.length === 0}
      <div class="state-message">
        <div class="empty-icon">⌕</div>
        <h3>{submissions.length === 0
          ? 'No submissions yet'
          : 'No matching submissions'}</h3>
        <p>
          {submissions.length === 0
            ? 'Submitted questionnaires will appear here.'
            : 'Try a different search term.'}
        </p>
      </div>
    {:else}
      <div class="submission-list">
        {#each filteredSubmissions as submission (submission.submission_id)}
          <article class="submission">
            <button
              class="submission-header"
              aria-expanded={expanded === submission.submission_id}
              onclick={() => toggleSubmission(submission.submission_id)}
            >
              <div class="submission-icon">▤</div>

              <div class="submission-main">
                <strong>Questionnaire submission</strong>
                <span>{formatDate(submission.submitted_at)}</span>
                <span class="submission-id">
                  ID: {submission.submission_id}
                </span>
              </div>

              <div class="submission-meta">
                <span class="answer-count">
                  {submission.answers.length}
                  {submission.answers.length === 1 ? 'answer' : 'answers'}
                </span>
                <span class="chevron">
                  {expanded === submission.submission_id ? '−' : '+'}
                </span>
              </div>
            </button>

            {#if expanded === submission.submission_id}
              <div class="submission-details">
                {#each submission.answers as answer, index (`${submission.submission_id}-${answer.question_id}-${index}`)}
                  <section class="answer-card">
                    <div class="answer-top">
                      <span class="record-number">
                        RECORD {index + 1}
                      </span>

                      <span class:grade-zero={answer.answer === 0} class="grade-badge">
                        {gradeLabel(answer.answer)}
                      </span>
                    </div>

                    <h3>{answer.term}</h3>

                    <div class="metadata">
                      <span><b>SOC</b> {answer.soc}</span>
                      <span><b>LLT code</b> {answer.llt_code}</span>
                      <span><b>Question ID</b> {answer.question_id}</span>
                    </div>

                    {#if answer.definition}
                      <div class="definition">
                        <span class="field-label">DEFINITION</span>
                        <p>{answer.definition}</p>
                      </div>
                    {/if}

                    <div class="selected-answer">
                      <div class="grade-number">{answer.answer}</div>
                      <div>
                        <strong>{gradeLabel(answer.answer)}</strong>
                        <p>
                          {answer.grade_description ??
                            'No grade description was recorded.'}
                        </p>
                      </div>
                    </div>
                  </section>
                {/each}
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

  .logo {
    width: 46px;
    height: 46px;
    display: grid;
    place-items: center;
    border-radius: 13px;
    background: #e6ecff;
    color: #4257bf;
    font-size: 23px;
    font-weight: 800;
  }

  .overline {
    margin: 0 0 5px;
    color: #6675b9;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: 1.4px;
  }

  h1 {
    margin: 0;
    font-size: 23px;
    letter-spacing: -0.5px;
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

  .intro {
    margin: 40px 0 24px;
  }

  .intro h2 {
    margin: 0;
    font-size: 29px;
    letter-spacing: -0.8px;
  }

  .intro > p:last-child {
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

  .count {
    padding: 6px 10px;
    border-radius: 20px;
    background: #f0f2f8;
    color: #596579;
    font-size: 12px;
    white-space: nowrap;
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
    background: white;
    text-align: left;
    cursor: pointer;
  }

  .submission-header:hover {
    background: #fafbfe;
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

  .answer-card {
    margin-top: 16px;
    padding: 19px;
    background: white;
    border: 1px solid #e6eaf1;
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
    background: #edf1ff;
    color: #4257bf;
    font-size: 11px;
    font-weight: 750;
    text-align: right;
  }

  .grade-badge.grade-zero {
    background: #e8f7ee;
    color: #187348;
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
    background: #f5f7fc;
  }

  .grade-number {
    width: 40px;
    height: 40px;
    flex-shrink: 0;
    display: grid;
    place-items: center;
    border-radius: 10px;
    background: #e3e9ff;
    color: #4257bf;
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
    to { transform: rotate(360deg); }
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

    .answer-card {
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
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      transition-duration: 0.01ms !important;
    }
  }
</style>
