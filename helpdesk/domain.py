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
    relation: tuple[str, ...] = ()
    evidence: tuple[dict, ...] = ()
    field_differences: tuple[dict, ...] = ()
    resolution_status: str = "UNKNOWN"


CONDITION_PATTERN = (r"\b(?:not|except|least|most|only|always|never|all|some|before|after|between|within|"
                     r"more|less|under|above|below|if|unless|must|\d+(?:\.\d+)?%?)\b")


def _conditions(text):
    # Preserve order: swapping the same numbers can change their roles or a range.
    return re.findall(CONDITION_PATTERN, (text or '').casefold())


def _negations(text):
    return sorted(re.findall(r'\b(?:not|except|never|neither|without)\b', (text or '').casefold()))


def compare(reference_version: str, reference: Question, reference_material: str | None,
            student_version: str, student: Question, student_material: str | None,
            *, student_raw_material: str | None = None) -> Comparison:
    """Conservative exact-content mapping; similarity never establishes equivalence."""
    def result(diffs, reason, mapping=()):
        evidence, changed, relations = [], [], []
        fields = [('passage', reference_material, student_material),
                  ('stem', reference.verified_stem, student.verified_stem)]
        ref_labels = {o.label: o for o in reference.options}
        stu_labels = {o.label: o for o in student.options}
        mapped = {student_id: reference_id for reference_id, student_id in mapping}
        ref_ids = {o.id: o for o in reference.options}
        for label in sorted(set('ABCD') | set(stu_labels) | set(ref_labels)):
            stu_option = stu_labels.get(label)
            ref_option = ref_ids[mapped[stu_option.id]] if stu_option and stu_option.id in mapped else ref_labels.get(label)
            fields.append(('option:' + label, ref_option.verified_text if ref_option else None,
                           stu_option.verified_text if stu_option else None))
        if reference.visual_evidence or student.visual_evidence or reference.visual_evidence is None or student.visual_evidence is None:
            fields.append(('visual_evidence', reference.visual_evidence, student.visual_evidence))
        for name, ref_text, stu_text in fields:
            observed = ref_text is not None and stu_text is not None and bool(ref_text.strip()) and bool(stu_text.strip())
            equal = observed and normalize(ref_text) == normalize(stu_text)
            item = {'field': name, 'reference_value': ref_text, 'student_value': stu_text,
                    'relation': 'EQUAL' if equal else 'DIFFERENT' if observed else 'UNKNOWN'}
            if name.startswith('option:') and mapping:
                option = next(o for o in student.options if o.label == name.split(':', 1)[1])
                item['reference_label'] = ref_ids[mapped[option.id]].label
                item['student_label'] = option.label
            evidence.append(item)
            if not observed:
                changed.append({**item, 'kind': 'UNVERIFIED_FIELD'})
            elif not equal and not mapping:
                critical = _conditions(ref_text) != _conditions(stu_text)
                negation = _negations(ref_text) != _negations(stu_text)
                number = sorted(re.findall(r'\b\d+(?:\.\d+)?\b', ref_text)) != sorted(re.findall(r'\b\d+(?:\.\d+)?\b', stu_text))
                same_frame = normalize(re.sub(CONDITION_PATTERN, '', ref_text.casefold())) == normalize(re.sub(CONDITION_PATTERN, '', stu_text.casefold()))
                changed.append({**item, 'kind': 'KEY_CONDITION_CONFLICT' if negation or number or critical and same_frame else 'CONTENT_DIFFERENCE'})
                if name == 'stem':
                    relations.append('STEM_CHANGED')
                if critical:
                    relations.append('CONDITION_CHANGED')
                if negation:
                    relations.append('NOT_DIFFERENCE')
        complete = (reference.complete and student.complete and bool(reference_material and reference_material.strip())
                    and bool(student_material and student_material.strip()))
        if not complete:
            # Raw OCR is evidence of an observation, never a verified condition.
            relations = ['UNKNOWN']
            changed = [{**item, 'kind': 'UNVERIFIED_FIELD' if item['relation'] == 'UNKNOWN' else 'OBSERVED_DIFFERENCE'}
                       for item in changed]
            if (student.verified_stem and reference.verified_stem
                    and normalize(student.verified_stem) == normalize(reference.verified_stem)
                    or student_raw_material and reference_material
                    and normalize(student_raw_material) in normalize(reference_material)):
                relations.append('PARTIAL_OBSERVATION')
        elif mapping:
            relations = ['SAME_CONTENT']
        elif reference_material is not None and student_material is not None and normalize(reference_material) == normalize(student_material):
            relations.append('SAME_PASSAGE_DIFFERENT_QUESTION' if Difference.UNCERTAIN not in diffs else 'UNKNOWN')
        else:
            relations.append('DIFFERENT_QUESTION')
        if Difference.NUMBER in diffs:
            relations.append('QUESTION_NUMBER_CHANGED')
            changed.append({'field': 'number', 'kind': 'NUMBER_ONLY', 'reference_value': reference.number, 'student_value': student.number})
        if Difference.ORDER in diffs:
            relations.append('OPTION_REORDER')
            changed.append({'field': 'options', 'kind': 'OPTION_ORDER', 'mapping': [list(pair) for pair in mapping]})
        status = 'INCOMPLETE' if not complete else 'MATCH_CANDIDATE' if mapping else 'UNKNOWN' if Difference.UNCERTAIN in diffs else 'MISMATCH'
        return Comparison(reference_version, student_version, tuple(diffs), tuple(mapping), reason,
                          tuple(dict.fromkeys(relations)), tuple(evidence), tuple(changed), status)

    if (not reference.complete or not student.complete or not reference_material or not reference_material.strip()
            or not student_material or not student_material.strip()):
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
