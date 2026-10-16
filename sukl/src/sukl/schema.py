"""The small, editable output contract sent to the model as JSON Schema."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


Frequency = Annotated[float, Field(ge=1)] | Literal[
    "very_common", "common", "uncommon", "rare", "very_rare", "not_known"
] | None
Action = Literal["continue", "discontinue_treatment", "contact_doctor", "hospitalize_NOW"]
EffectType = Literal[
    "contraindicated", "synergy", "antagonism", "increased_toxicity",
    "increased_exposure", "decreased_exposure",
]


class ActionCondition(Record):
    patient_guide: str = Field(min_length=1, description="Plain Czech condition addressed to the patient: when the action applies. Preserve source qualifiers.")


class SideEffect(Record):
    ctcae: str | None = Field(description="Exact English CTCAE v6 term, or null if no suitable match.")
    frequency: Frequency = Field(description="One in N for an explicit point rate; category for a range; null if absent.")
    label: str = Field(description="Czech symptom description, preserving dose/population/severity qualifiers.")
    action: Action | None = Field(description="Source-supported action; null when no recommendation is given.")
    action_condition: ActionCondition | None = Field(description="Patient-facing condition for the action; null if unconditional or no action is given.")


class DrugCombination(Record):
    drug: str
    effect: str = Field(description="Czech description of the source-supported interaction, including conditions and recommendations.")
    effect_type: EffectType | None = Field(description="Source-supported interaction category for grouping; null if unclear. Prefer contraindicated, then increased_toxicity when multiple categories apply.")
    side_effects: list[SideEffect]


class DrugClassCombination(Record):
    drug_class: str
    effect: str = Field(description="Czech description of the source-supported interaction, including conditions and recommendations.")
    effect_type: EffectType | None = Field(description="Source-supported interaction category for grouping; null if unclear. Prefer contraindicated, then increased_toxicity when multiple categories apply.")
    side_effects: list[SideEffect]


class DrugExtraction(Record):
    side_effects: list[SideEffect]
    combinations: list[DrugCombination | DrugClassCombination]
