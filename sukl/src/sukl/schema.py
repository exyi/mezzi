"""The small, editable output contract sent to the model as JSON Schema."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


Frequency = Annotated[float, Field(ge=1)] | Literal["very_common", "common", "uncommon", "rare", "very_rare", "not_known"] | None
Action = Literal["continue", "discontinue_treatment", "contact_doctor", "emergency"]
EffectType = Literal["contraindicated", "synergy", "antagonism", "increased_toxicity", "increased_exposure", "decreased_exposure", "changed_exposure"]


class ActionCondition(Record):
    patient_guide: str | None = Field(min_length=1, description="Plain Czech condition describing only WHEN the action applies and has some other condition than the side-effect itself, without action commands. Preserve source qualifiers and recognizable symptoms.")
    min_severity: int | None = Field(description="Minimum CTCAEv6 grade for this side effect to be serious enough for the action to apply. Range from 1 to 5, but normally 1 or 2, since 3 requires doctor anyway. None if not known.")


class SideEffect(Record):
    ctcae: str | None = Field(description="Equivalent exact English CTCAE v6 term. Null if no suitable match.")
    frequency: Frequency = Field(description="One in N for an explicit point rate for this exact event and scope; source category otherwise; null if absent. Never treat a grade-specific rate as an all-grade rate.")
    label: str = Field(description="Czech symptom description, preserving dose/population/severity qualifiers.")
    action: Action | None = Field(description="Reaction-specific patient advice from the source; most urgent if simultaneous. Generic side-effect reporting and clinician-only treatment plans do not establish an action. Null if absent.")
    action_condition: ActionCondition | None = Field(description="Patient-facing condition for the action; null if unconditional or no action is given.")


# Shared vocabulary for pharmacological properties that ATC does not encode.
FUNCTIONAL_CLASSES = {
    "group:cyp3a4_inhibitor": "CYP3A4 inhibitor (any strength)",
    "group:cyp3a4_inhibitor_strong": "Strong CYP3A4 inhibitor",
    "group:cyp3a4_inhibitor_moderate": "Moderate CYP3A4 inhibitor",
    "group:cyp3a4_inducer": "CYP3A4 inducer (any strength)",
    "group:cyp3a4_inducer_strong": "Strong CYP3A4 inducer",
    "group:cyp2d6_inhibitor": "CYP2D6 inhibitor (any strength)",
    "group:cyp2d6_inhibitor_strong": "Strong CYP2D6 inhibitor",
    "group:cyp2c9_inhibitor": "CYP2C9 inhibitor",
    "group:cyp2c19_inhibitor": "CYP2C19 inhibitor",
    "group:pgp_inhibitor": "P-glycoprotein inhibitor",
    "group:pgp_inducer": "P-glycoprotein inducer",
    "group:qt_prolonging": "Medicine that prolongs the QT interval",
    "group:serotonergic": "Serotonergic medicine",
    "group:cns_depressant": "Central nervous system depressant",
    "group:hyperkalemia_risk": "Medicine that increases potassium",
    "group:hypokalemia_risk": "Medicine that lowers potassium",
    "group:nephrotoxic": "Nephrotoxic medicine",
    "group:myelosuppressive": "Myelosuppressive medicine",
}
FunctionalClass = Literal[tuple(FUNCTIONAL_CLASSES)]
ClassId = Annotated[str, Field(pattern=r"^(atc:[A-Z][0-9]{2}(?:[A-Z](?:[A-Z](?:[0-9]{2})?)?)?|atc:[A-Z]|substance:[0-9]+)$")] | FunctionalClass
# Match combination.class_id by exact membership in another record's classes.
# atc:CODE includes all CSV ATC ancestors; substance:CODE uses KOD_LATKY from
# active composition rows. group:* membership is classified by the LLM using
# FUNCTIONAL_CLASSES. A match is a candidate: effect still carries route, dose,
# exclusions and required multidrug regimen conditions. Null means unclassified.
TARGET_ID_DESCRIPTION = "Standardized target ID: atc:CODE from the supplied ATC vocabulary, substance:KOD_LATKY verified using CSV metadata for a named compound, or a supplied group:* ID. Choose by meaning, not spelling. Null if no equivalent ID; never broaden a qualified target or a multidrug regimen."


class DrugCombination(Record):
    class_id: ClassId | None = Field(description=TARGET_ID_DESCRIPTION)
    drug: str = Field(description="Named interacting substance, matching the actual effect text. You can search related documents.")
    effect: str = Field(description="Patient-friendly Czech interaction description: affected drug, mechanisms and source-supported conditions. Describe dose changes and monitoring as clinician actions.")
    effect_type: EffectType | None = Field(description="Source-supported interaction category for grouping; null if unclear. Prefer contraindicated, then increased_toxicity when multiple categories apply.")
    side_effects: list[SideEffect]


class DrugClassCombination(Record):
    class_id: ClassId | None = Field(description=TARGET_ID_DESCRIPTION)
    drug_class: str = Field(description="One coherent interacting class or functional group; preserve any required multidrug regimen. Write in CZECH.")
    effect: str = Field(description="Patient-friendly Czech interaction description: affected drug, mechanisms and source-supported conditions. Describe dose changes and monitoring as clinician actions.")
    effect_type: EffectType | None = Field(description="Source-supported interaction category for grouping; null if unclear. Prefer contraindicated, then increased_toxicity when multiple categories apply.")
    side_effects: list[SideEffect]


class DrugExtraction(Record):
    classes: list[FunctionalClass] = Field(description="All applicable supplied group:* IDs for the TARGET drug, classified from PIL/SPC pharmacology. Include general and strength-specific groups where applicable. ATC and substance IDs are added from CSVs by Python. Do not infer membership merely because this drug interacts with a member of the group.")
    side_effects: list[SideEffect]
    combinations: list[DrugCombination | DrugClassCombination]
    usage: str = Field(description="Very short decription of what is the medication used for.")


class DrugOutput(DrugExtraction):
    schema_version: Literal[3]
    metadata: dict
    classes: list[ClassId]
