import groups from '../../../../se-translation/assets/ctcae-groups-questions-cz.json' with { type: 'json' };

export type QuestionStep = {
  kind: 'question';
  questionIndex: number;
};

export type GroupStep = {
  kind: 'group';
  group: (typeof groups)[number];
  questionIndices: number[];
};

export type Step = GroupStep | QuestionStep;
export type GroupAnswers = Record<string, boolean>;
export type Answers = Record<number, number>;

export function buildSteps(questions: { id: string | number }[]): Step[] {
  // Only gate groups with at least three questions in this questionnaire.
  const gates: GroupStep[] = groups.map((group) => ({
    kind: 'group' as const,
    group,
    questionIndices: questions.flatMap((question, index) =>
      group.ids.includes(Number(question.id)) ? [index] : []
    )
  })).filter((step) => step.questionIndices.length >= 3);

  return questions.flatMap<Step>((_, index) => {
    const gate = gates.find((step) => step.questionIndices.includes(index));
    if (!gate) return [{ kind: 'question', questionIndex: index }];
    return gate.questionIndices[0] === index ? [gate] : [];
  });
}

export function visibleSteps(steps: Step[], groupAnswers: GroupAnswers): Step[] {
  return steps.flatMap<Step>((step) => {
    if (step.kind === 'question' || groupAnswers[step.group.group] !== true) {
      return [step];
    }
    return [step, ...step.questionIndices.map((questionIndex): QuestionStep => ({
      kind: 'question', questionIndex
    }))];
  });
}

export function effectiveAnswers(
  steps: Step[],
  answers: Answers,
  groupAnswers: GroupAnswers
): Answers {
  const result = { ...answers };
  for (const step of steps) {
    if (step.kind === 'group' && groupAnswers[step.group.group] === false) {
      for (const index of step.questionIndices) result[index] = 0;
    }
  }
  return result;
}
