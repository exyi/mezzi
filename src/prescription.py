from dataclasses import dataclass


@dataclass(frozen=True)
class DrugPrescription:
    patient_id: str | None  # pseudonym from the Patient resource
    doc_id: str  # DocumentReference id
    doc_type: str | None  # unis-typ-zaznamu code, e.g. ReceRadter / RatpRadter
    is_prescription: bool  # True if the document is LOINC 57833-6
    date: str | None  # ISO date (YYYY-MM-DD) of the document
    sukl: str  # 7-digit SUKL drug code
    product: str  # raw product string, e.g. "CALTRATE D3/600MG/400IU"
    name: str  # product name, e.g. "CALTRATE D3"
    strength: str | None  # e.g. "600MG/400IU"
    form: str  # e.g. "TBL FLM"
    pack: str  # e.g. "90/1bal."
    dosing: str  # remainder of the line, e.g. "1xd /rep. 2x"
    source_file: str | None = None
    # activity analysis: filled in by find_meds_json by some heuristics
    status: str | None = None  # MedicationRequest.status, if item came from one
    supply_days: int | None = None  # estimated days of supply from pack + dosing
    is_prn: bool = False  # True if "as needed" (dle potřeby / dp / při ...)
    active_until: str | None = None  # ISO date the estimated supply runs out
    active: bool | None = None  # True if likely still active at the reference date
