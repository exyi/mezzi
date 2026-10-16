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
    "increased_exposure", "decreased_exposure", "changed_exposure",
]


class ActionCondition(Record):
    patient_guide: str = Field(min_length=1, description="Plain Czech condition describing only WHEN the action applies and has some other condition than the side-effect itself, without action commands. Preserve source qualifiers and recognizable symptoms.")


class SideEffect(Record):
    ctcae: str | None = Field(description="Equivalent exact English CTCAE v6 term. Null if no suitable match.")
    frequency: Frequency = Field(description="One in N for an explicit point rate for this exact event and scope; source category otherwise; null if absent. Never treat a grade-specific rate as an all-grade rate.")
    label: str = Field(description="Czech symptom description, preserving dose/population/severity qualifiers.")
    action: Action | None = Field(description="Reaction-specific patient advice from the source; most urgent if simultaneous. Generic side-effect reporting and clinician-only treatment plans do not establish an action. Null if absent.")
    action_condition: ActionCondition | None = Field(description="Patient-facing condition for the action; null if unconditional or no action is given.")


class DrugCombination(Record):
    drug: str = Field(description="Named interacting substance, matching the actual effect text. You can search related documents.")
    effect: str = Field(description="Patient-friendly Czech interaction description: affected drug, mechanisms and source-supported conditions. Describe dose changes and monitoring as clinician actions.")
    effect_type: EffectType | None = Field(description="Source-supported interaction category for grouping; null if unclear. Prefer contraindicated, then increased_toxicity when multiple categories apply.")
    side_effects: list[SideEffect]


class DrugClassCombination(Record):
    drug_class: str = Field(description="One coherent interacting class or functional group; preserve any required multidrug regimen. Write in CZECH.")
    effect: str = Field(description="Patient-friendly Czech interaction description: affected drug, mechanisms and source-supported conditions. Describe dose changes and monitoring as clinician actions.")
    effect_type: EffectType | None = Field(description="Source-supported interaction category for grouping; null if unclear. Prefer contraindicated, then increased_toxicity when multiple categories apply.")
    side_effects: list[SideEffect]


class DrugExtraction(Record):
    side_effects: list[SideEffect]
    combinations: list[DrugCombination | DrugClassCombination]
