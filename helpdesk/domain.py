from __future__ import annotations

from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from enum import StrEnum
import re
import unicodedata
from uuid import uuid4


def new_id() -> str:
    return uuid4().hex


def normalize(text: str) -> str:
    # Only typography/whitespace: preserve negation, numbers, case, tense and punctuation.
    return " ".join(unicodedata.normalize("NFC", text).split())


class Intent(StrEnum):
    NEW = "NEW"
    FOLLOWUP = "FOLLOWUP"
    SUBQUESTION = "SUBQUESTION"
    SUPPLEMENT = "SUPPLEMENT"
    CORRECTION = "CORRECTION"
    DISPUTE = "DISPUTE"
    IRRELEVANT = "IRRELEVANT"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Option:
    id: str
    label: str
    order: int
    raw_text: str
    verified_text: str | None
    source: str

    @classmethod
    def confirmed(cls, label: str, text: str, order: int, source: str) -> Option:
        return cls(new_id(), label, order, text, text, source)


@dataclass(frozen=True)
class Question:
    number: str
    raw_stem: str
    verified_stem: str | None
    options: tuple[Option, ...]
    source: str
    uncertain_fields: tuple[str, ...] = ()
    kind: str = "single_choice"
    visual_evidence: str | None = "" # None means required image/table evidence is unverified.

    def __post_init__(self):
        if not self.source or not self.number:
            raise ValueError("Question number and provenance are required")
        for attr in ("id", "label", "order"):
            values = [getattr(o, attr) for o in self.options]
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate option {attr}")
        if any(not o.source for o in self.options):
            raise ValueError("Option provenance is required")

    @property
    def complete(self) -> bool:
        return (bool(self.verified_stem and self.verified_stem.strip()) and not self.uncertain_fields
                and self.visual_evidence is not None and self.kind == "single_choice"
                and len(self.options) == 4 and {o.label for o in self.options} == set("ABCD")
                and all(bool(o.verified_text and o.verified_text.strip()) for o in self.options))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Question:
        return cls(**{**data, "options": tuple(Option(**o) for o in data["options"]),
                      "uncertain_fields": tuple(data.get("uncertain_fields", ()))})


class Difference(StrEnum):
    FORMATTING = "FORMATTING_ONLY"
    NUMBER = "NUMBER_ONLY"
    ORDER = "OPTION_ORDER"
    SUBSTANTIVE = "SUBSTANTIVE"
    UNCERTAIN = "UNCERTAIN"
    DIFFERENT = "DIFFERENT_QUESTION"


@dataclass(frozen=True)
class Comparison:
    reference_version: str
    student_version: str
    differences: tuple[Difference, ...]
    option_mapping: tuple[tuple[str, str], ...]
    reason: str


def compare(reference_version: str, reference: Question, reference_material: str | None,
            student_version: str, student: Question, student_material: str | None) -> Comparison:
    """Conservative exact-content mapping; similarity never establishes equivalence."""
    def result(diffs, reason, mapping=()):
        return Comparison(reference_version, student_version, tuple(diffs), tuple(mapping), reason)

    if not reference.complete or not student.complete or reference_material is None or student_material is None:
        return result([Difference.UNCERTAIN], "Unverified fields; keep candidate completion separate")
    material_equal = normalize(reference_material) == normalize(student_material)
    stem_equal = normalize(reference.verified_stem) == normalize(student.verified_stem)
    if not material_equal and not stem_equal:
        return result([Difference.DIFFERENT], "Material and stem differ; no migration")
    if not material_equal or not stem_equal or reference.visual_evidence != student.visual_evidence:
        return result([Difference.SUBSTANTIVE], "Material, stem or visual evidence changed")
    if reference.kind != "single_choice" or student.kind != "single_choice":
        return result([Difference.UNCERTAIN], "Unsupported question kind")
    for question in (reference, student):
        if len(question.options) != 4 or {o.label for o in question.options} != set("ABCD"):
            return result([Difference.UNCERTAIN], "Requires exactly four A-D options")
        texts = [normalize(o.verified_text) for o in question.options]
        position_dependent = r"\b(?:both|either|neither|all|none|above|below)\b|以上|下列|上述|两项"
        if any(re.search(position_dependent, text, re.I) or re.search(r"\b[A-D]\b", text) for text in texts):
            return result([Difference.UNCERTAIN], "Potential label/order dependency; manual review")
        for i, text in enumerate(texts):
            for other in texts[i + 1:]:
                if text == other or SequenceMatcher(None, text.casefold(), other.casefold()).ratio() >= .90:
                    return result([Difference.UNCERTAIN], "Duplicate or near-duplicate options; no mapping")
    ref = {normalize(o.verified_text): o for o in reference.options}
    stu = {normalize(o.verified_text): o for o in student.options}
    if ref.keys() != stu.keys():
        return result([Difference.SUBSTANTIVE], "Option contents changed")
    diffs = []
    if reference.number != student.number:
        diffs.append(Difference.NUMBER)
    if any((ref[t].label, ref[t].order) != (stu[t].label, stu[t].order) for t in ref):
        diffs.append(Difference.ORDER)
    return result(diffs or [Difference.FORMATTING], "Verified exact-content bijection",
                  [(ref[t].id, stu[t].id) for t in ref])


def mapped_label(comparison: Comparison, reference_version: str, student_version: str,
                 correct_reference_option_id: str, student: Question) -> str:
    if (reference_version, student_version) != (comparison.reference_version, comparison.student_version):
        raise ValueError("Mapping belongs to different versions")
    mapping = dict(comparison.option_mapping)
    if len(mapping) != 4 or correct_reference_option_id not in mapping:
        raise ValueError("No verified one-to-one mapping")
    return next(o.label for o in student.options if o.id == mapping[correct_reference_option_id])
