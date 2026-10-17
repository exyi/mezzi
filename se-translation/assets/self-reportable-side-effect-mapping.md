# Self-reportable side-effect remapping

`self-reportable-side-effect-remapping.csv` contains only non-exact mappings to existing questions. Each row has two fields: `source_term` (the source CTCAE term) and `target_question_id`.

Exact matches use the questionnaire annotation table directly and are not repeated in this CSV. Source terms are compared after trimming whitespace and ignoring case. Every CSV mapping applies unconditionally.

| Source term | Target question ID | Target term |
|---|---:|---|
| Vertigo | 40 | Dizziness |
| Abdominal distension | 13 | Bloating |
| Purpura | 73 | Bruising |

These broader mappings lose detail; the generator retains the original source label. Conditional mappings and label-based rules have been removed because the prototype cannot reliably evaluate them. Unmatched terms select no question.

This table selects questions only; it does not assign severity grades. Medication action conditions are separate and remain attached to their source recommendations.
