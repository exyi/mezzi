"""The evolving domain contract. Keep provider and storage details out of this file."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(Record):
    document_id: str = Field(description="Exact ID supplied by the corpus tools.")
    page: int = Field(ge=1, description="1-based PDF page, not the printed page number.")
    quote: str = Field(min_length=8, max_length=1800, description="Verbatim supporting passage.")


class Frequency(Record):
    category: Literal[
        "very_common", "common", "uncommon", "rare", "very_rare", "not_known", "exact"
    ]
    source_text: str = Field(description="Original frequency wording, including qualifiers.")
    population: str | None = Field(default=None, description="Dose, indication, population, duration.")
    exact_probability: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def exact_only(self) -> Self:
        if (self.category == "exact") != (self.exact_probability is not None):
            raise ValueError("exact_probability must be present only for category=exact")
        return self

    @property
    def interval(self) -> tuple[float, float, bool, bool] | None:
        """lower, upper, lower inclusive, upper inclusive; never choose a midpoint."""
        return {
            "very_common": (0.1, 1.0, True, True),
            "common": (0.01, 0.1, True, False),
            "uncommon": (0.001, 0.01, True, False),
            "rare": (0.0001, 0.001, True, False),
            "very_rare": (0.0, 0.0001, True, False),
            "exact": (self.exact_probability, self.exact_probability, True, True),
        }.get(self.category)

    @property
    def one_in(self) -> float | None:
        """A display convenience, only when the source actually supplies a point rate."""
        return 1 / self.exact_probability if self.exact_probability is not None else None


Action = Literal["continue", "discontinue_treatment", "contact_doctor", "hospitalize_NOW"]


class SideEffect(Record):
    label: str = Field(min_length=1, description="Czech source label; do not lose symptom qualifiers.")
    ctcae: str | None = Field(default=None, description="Exact English CTCAE v6 term or null.")
    ctcae_evidence: list[Citation] = Field(default_factory=list)
    mapping_note: str | None = None
    frequency: Frequency | None = None
    action: Action | None = Field(default=None, description="Only an explicitly supported action.")
    action_condition: str | None = Field(default=None, description="Symptoms/severity that trigger action.")
    action_evidence: list[Citation] = Field(default_factory=list)
    evidence: list[Citation] = Field(min_length=1, description="Drug-document support for event and rate.")

    @model_validator(mode="after")
    def require_support(self) -> Self:
        if self.ctcae is not None and not self.ctcae_evidence:
            raise ValueError("CTCAE mappings require CTCAE evidence; otherwise use null")
        if self.action is not None and not self.action_evidence:
            raise ValueError("Actions require explicit drug-document evidence; otherwise use null")
        return self


class Combination(Record):
    drug: str | None = None
    drug_class: str | None = None
    effect: str = Field(description="Interaction, including decreased efficacy or PK changes.")
    recommendation: str | None = Field(default=None, description="Source recommendation, if present.")
    side_effects: list[SideEffect] = Field(default_factory=list)
    evidence: list[Citation] = Field(min_length=1)

    @model_validator(mode="after")
    def one_target(self) -> Self:
        if bool(self.drug) == bool(self.drug_class):
            raise ValueError("Specify exactly one of drug and drug_class")
        return self


class DrugExtraction(Record):
    side_effects: list[SideEffect]
    combinations: list[Combination]
    uncertainties: list[str] = Field(
        default_factory=list,
        description="Conflicts, unmapped terms, ambiguous tables, missing documents or coverage.",
    )
