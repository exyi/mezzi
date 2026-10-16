"""The small, editable output contract sent to the model as JSON Schema."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


Frequency = Annotated[float, Field(ge=1)] | Literal[
    "very_common", "common", "uncommon", "rare", "very_rare", "not_known"
] | None
Action = Literal["continue", "discontinue_treatment", "contact_doctor", "hospitalize_NOW"]


class SideEffect(Record):
    ctcae: str | None = Field(description="Exact English CTCAE v6 term, or null if no suitable match.")
    frequency: Frequency = Field(description="One in N for an explicit point rate; category for a range; null if absent.")
    label: str = Field(description="Czech symptom description, preserving dose/population/severity qualifiers.")
    action: Action | None = Field(description="Source-supported action; null when no recommendation is given.")


class DrugCombination(Record):
    drug: str
    effect: str = Field(description="Czech description of the source-supported interaction, including conditions and recommendations.")
    side_effects: list[SideEffect]


class DrugClassCombination(Record):
    drug_class: str
    effect: str = Field(description="Czech description of the source-supported interaction, including conditions and recommendations.")
    side_effects: list[SideEffect]


class DrugExtraction(Record):
    side_effects: list[SideEffect]
    combinations: list[DrugCombination | DrugClassCombination]
