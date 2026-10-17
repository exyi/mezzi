"""The small, editable output contract sent to the model as JSON Schema."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


Frequency = Annotated[float, Field(ge=1)] | Literal["very_common", "common", "uncommon", "rare", "very_rare", "not_known"] | None
Action = Literal["continue", "discontinue_treatment", "contact_doctor", "emergency"]
EffectType = Literal["contraindicated", "synergy", "antagonism", "increased_toxicity", "increased_exposure", "decreased_exposure", "changed_exposure"]


class ActionCondition(Record):
    patient_guide: str | None = Field(min_length=1, description="Plain Czech additional condition for the action beyond the event and severity threshold, without action commands. Preserve source qualifiers and recognizable symptoms. Null if no additional condition.")
    min_severity: int | None = Field(description="Inferred precautionary CTCAE action threshold, not a source-reported grade. Default 3; lower to 2 for explicit extra attention or early warning signs of a worse problem, and to 1 for advice to act at the first sign or regardless of severity. Explicit source thresholds take precedence. Null only if severity grading is not meaningful. Do not invent or escalate the action.")


class SideEffect(Record):
    model_config = ConfigDict(extra="allow")

    ctcae: str | None = Field(description="Exact term copied from the supplied English CTCAE v6 list, equivalent in meaning without adding a subtype or cause. Null if no suitable match.")
    frequency: Frequency = Field(description="For explicit incidence p%, use 100/p, not p (1.3% -> 76.9231). Match event, dose, population and severity; never use percentage changes in lab values or comparator rates. Source category otherwise; null if absent.")
    label: str = Field(description="Plain Czech event description with source-supported explanations and dose/population/severity qualifiers, including the scope of any numeric rate. Merge synonyms; do not invent a more specific diagnosis.")
    action: Action | None = Field(description="Reaction-specific source advice; most urgent if simultaneous. emergency requires explicit urgent medical help. Generic reporting, driving precautions and clinician dose changes do not establish continue or another action. Null if absent.")
    action_condition: ActionCondition | None = Field(description="Null if action is null. Otherwise include the inferred min_severity threshold (default 3, lower for explicit warning advice); patient_guide is null unless there is an additional source-supported condition. No action commands.")


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
TARGET_ID_DESCRIPTION = "Standardized target ID: atc:CODE from the supplied ATC vocabulary, substance:KOD_LATKY verified using CSV metadata for a named compound, or a supplied group:* ID. Named compounds require substance IDs, not ATC/property IDs. For classes, require equivalent meaning, not a related broader/narrower group. Null if unresolved or no equivalent ID; never broaden a qualified target or a multidrug regimen."


class DrugCombination(Record):
    class_id: ClassId | None = Field(description=TARGET_ID_DESCRIPTION)
    drug: str = Field(description="One named interacting substance matching the effect text; exact CSV Czech name when available. Verify substance:KOD_LATKY through active CSV composition; do not replace it with ATC or a property group.")
    effect: str = Field(description="Patient-friendly Czech interaction description: affected drug, mechanisms and source-supported conditions. Describe dose changes and monitoring as clinician actions.")
    effect_type: EffectType | None = Field(description="Source-supported category; changed_exposure only for an explicit pharmacokinetic interaction of unspecified/mixed direction, not dose adjustment alone. Prefer contraindicated (preserve conditions), then increased_toxicity; null if unclear.")
    side_effects: list[SideEffect]


class DrugClassCombination(Record):
    class_id: ClassId | None = Field(description=TARGET_ID_DESCRIPTION)
    drug_class: str = Field(description="One coherent interacting class or functional group; preserve any required multidrug regimen. Write in CZECH.")
    effect: str = Field(description="Patient-friendly Czech interaction description: affected drug, mechanisms and source-supported conditions. Describe dose changes and monitoring as clinician actions.")
    effect_type: EffectType | None = Field(description="Source-supported category; changed_exposure only for an explicit pharmacokinetic interaction of unspecified/mixed direction, not dose adjustment alone. Prefer contraindicated (preserve conditions), then increased_toxicity; null if unclear.")
    side_effects: list[SideEffect]


class DrugExtraction(Record):
    classes: list[FunctionalClass] = Field(description="All applicable supplied group:* IDs for the TARGET drug, classified from PIL/SPC pharmacology. Include general and strength-specific groups where applicable. ATC and substance IDs are added from CSVs by Python. Do not infer membership merely because this drug interacts with a member of the group.")
    side_effects: list[SideEffect]
    combinations: list[DrugCombination | DrugClassCombination]
    usage: str = Field(description="One short, patient-friendly Czech sentence explaining what the target medication is used for.")


class DrugOutput(DrugExtraction):
    schema_version: Literal[3]
    metadata: dict
    classes: list[ClassId]
