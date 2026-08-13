"""Core data model: PII types, detected entity spans, and conflict resolution.

Every recognizer in this package speaks the same language -- it takes a string
and yields `Entity` objects. That uniformity is what makes adding a new PII
type cheap (see README, "Extending to a new PII type").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List


class PIIType(str, Enum):
    """The PII categories this tool detects.

    The first nine are mandated by the assignment brief. The remainder are
    India-specific identifiers that appear in (or plausibly appear in) offer
    documents; they are detected by default and can be disabled via config.
    """

    # --- Required by the brief ---
    PERSON = "PERSON"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ORGANISATION = "ORGANISATION"
    ADDRESS = "ADDRESS"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    IP_ADDRESS = "IP_ADDRESS"

    # --- India-specific extensions ---
    PAN = "PAN"
    AADHAAR = "AADHAAR"
    DIN = "DIN"
    CIN = "CIN"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    WEBSITE = "WEBSITE"


#: Types the assignment explicitly asks for. Used to scope the headline metrics.
REQUIRED_TYPES = [
    PIIType.PERSON,
    PIIType.EMAIL,
    PIIType.PHONE,
    PIIType.ORGANISATION,
    PIIType.ADDRESS,
    PIIType.SSN,
    PIIType.CREDIT_CARD,
    PIIType.DATE_OF_BIRTH,
    PIIType.IP_ADDRESS,
]


@dataclass(frozen=True)
class Entity:
    """One detected PII span within a single block of text.

    Attributes:
        start: Inclusive character offset into the source text.
        end: Exclusive character offset into the source text.
        pii_type: Which category this span belongs to.
        text: The matched substring (kept for logging and surrogate keying).
        score: Detector confidence in [0, 1]. Used to break overlaps.
        source: Which recognizer produced it, for auditability.
    """

    start: int
    end: int
    pii_type: PIIType
    text: str
    score: float = 1.0
    source: str = "unknown"

    def __len__(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Entity") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass
class RedactionStats:
    """Running tally of what was replaced, for the run report."""

    counts: dict = field(default_factory=dict)

    def record(self, pii_type: PIIType) -> None:
        self.counts[pii_type.value] = self.counts.get(pii_type.value, 0) + 1

    def total(self) -> int:
        return sum(self.counts.values())


def resolve_overlaps(entities: Iterable[Entity]) -> List[Entity]:
    """Collapse overlapping detections into one non-overlapping sequence.

    Two recognizers frequently claim the same characters -- e.g. an ADDRESS
    span swallowing an ORGANISATION name, or a PERSON name detected by both the
    gazetteer and the NER layer. We keep the span that is (1) longer, then
    (2) higher-confidence, since the longer span redacts strictly more text and
    is therefore the safer choice for recall.

    Args:
        entities: Detections in any order, possibly overlapping.

    Returns:
        Entities sorted by start offset, guaranteed non-overlapping.
    """
    ordered = sorted(entities, key=lambda e: (-len(e), -e.score, e.start))
    kept: List[Entity] = []
    for candidate in ordered:
        if not any(candidate.overlaps(k) for k in kept):
            kept.append(candidate)
    return sorted(kept, key=lambda e: e.start)
