"""Pattern-based recognizers for structurally regular PII.

Structured identifiers (emails, cards, IPs, PANs) have rigid grammars, so
regex beats a statistical model on both precision and speed. Where a raw
pattern would be too loose to be safe -- dates, DINs, bank accounts -- the
pattern is paired with either a checksum (Luhn, Verhoeff) or a required
context keyword within a short window.

Every recognizer here subclasses `RegexRecognizer` and is registered in
`REGEX_RECOGNIZERS` at the bottom of the file. Adding a new structured PII
type means adding one small class and one list entry.
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Pattern

from ..entities import Entity, PIIType


class RegexRecognizer:
    """Base class: apply a compiled pattern, optionally filter the matches.

    Subclasses override `validate()` to reject structurally-valid-but-wrong
    matches (e.g. a 16-digit number that fails the Luhn checksum).
    """

    pii_type: PIIType
    pattern: Pattern[str]
    score: float = 0.95
    #: If set, the match is only accepted when one of these keywords appears
    #: within `context_window` characters before the match.
    context_keywords: Optional[List[str]] = None
    context_window: int = 60
    #: Which regex group holds the span to redact. 0 = whole match.
    group: int = 0

    @property
    def name(self) -> str:
        return type(self).__name__

    def validate(self, match: re.Match) -> bool:  # noqa: D401 - simple hook
        """Return True if this match is genuinely PII. Overridden by subclasses."""
        return True

    def _context_ok(self, text: str, start: int) -> bool:
        if not self.context_keywords:
            return True
        window = text[max(0, start - self.context_window): start].lower()
        return any(keyword in window for keyword in self.context_keywords)

    def analyse(self, text: str) -> Iterator[Entity]:
        for match in self.pattern.finditer(text):
            if not self._context_ok(text, match.start()):
                continue
            if not self.validate(match):
                continue
            start, end = match.span(self.group)
            yield Entity(
                start=start,
                end=end,
                pii_type=self.pii_type,
                text=text[start:end],
                score=self.score,
                source=self.name,
            )


# --------------------------------------------------------------------------
# Checksum helpers
# --------------------------------------------------------------------------

def luhn_valid(number: str) -> bool:
    """Standard Luhn mod-10 check used by all major card networks."""
    digits = [int(d) for d in re.sub(r"\D", "", number)]
    if len(digits) < 13:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    """Verhoeff checksum -- the algorithm UIDAI uses for Aadhaar numbers."""
    digits = re.sub(r"\D", "", number)
    if len(digits) != 12:
        return False
    checksum = 0
    for index, digit in enumerate(reversed(digits)):
        checksum = _VERHOEFF_D[checksum][_VERHOEFF_P[index % 8][int(digit)]]
    return checksum == 0


# --------------------------------------------------------------------------
# Recognizers -- required types
# --------------------------------------------------------------------------

class EmailRecognizer(RegexRecognizer):
    pii_type = PIIType.EMAIL
    score = 0.99
    pattern = re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}\b"
    )


class PhoneRecognizer(RegexRecognizer):
    """Indian landline/mobile formats plus generic international numbers.

    Deliberately anchored on either a `+<country code>` prefix or a leading 0,
    because a bare 10-digit run in a prospectus is far more likely to be a
    share count or a rupee figure than a telephone number.
    """

    pii_type = PIIType.PHONE
    score = 0.95
    pattern = re.compile(
        r"""(?<![\d.])(?:
              \+\s?\d{1,3}[\s\-]?(?:\(\d{1,4}\)[\s\-]?)?\d{2,5}[\s\-]?\d{3,5}[\s\-]?\d{0,5}
            | 0\d{2,4}[\s\-]\d{6,8}
        )(?!\d)(?!\.\d)""",
        re.VERBOSE,
    )

    def validate(self, match: re.Match) -> bool:
        digits = re.sub(r"\D", "", match.group(0))
        return 8 <= len(digits) <= 15


class IPAddressRecognizer(RegexRecognizer):
    """IPv4 with per-octet range validation, plus common IPv6 forms."""

    pii_type = PIIType.IP_ADDRESS
    score = 0.99
    pattern = re.compile(
        r"""(?<![\w.])(?:
              (?:\d{1,3}\.){3}\d{1,3}
            | (?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}
        )(?!\w)(?!\.\d)""",
        re.VERBOSE,
    )

    def validate(self, match: re.Match) -> bool:
        value = match.group(0)
        if ":" in value:
            return True
        return all(0 <= int(octet) <= 255 for octet in value.split("."))


class SSNRecognizer(RegexRecognizer):
    """US Social Security Numbers, excluding structurally invalid ranges."""

    pii_type = PIIType.SSN
    score = 0.97
    pattern = re.compile(r"\b(\d{3})[-\s](\d{2})[-\s](\d{4})\b")

    def validate(self, match: re.Match) -> bool:
        area, group, serial = match.groups()
        if area in {"000", "666"} or area.startswith("9"):
            return False
        return group != "00" and serial != "0000"


class CreditCardRecognizer(RegexRecognizer):
    """13-19 digit card numbers that pass Luhn.

    The checksum is what keeps this from firing on invoice numbers and
    share-count figures; roughly 90% of random digit strings fail it.
    """

    pii_type = PIIType.CREDIT_CARD
    score = 0.98
    pattern = re.compile(r"(?<![\d.])(?:\d[ \-]?){12,18}\d(?!\d)(?!\.\d)")

    def validate(self, match: re.Match) -> bool:
        return luhn_valid(match.group(0))


class DateOfBirthRecognizer(RegexRecognizer):
    """Dates, but only when a birth-related keyword precedes them.

    An offer document is saturated with dates (board resolutions, certificates,
    bid windows). Without the context gate this recognizer would have near-zero
    precision, so the gate is the single most important line in this class.
    """

    pii_type = PIIType.DATE_OF_BIRTH
    score = 0.9
    context_keywords = ["date of birth", "birth date", "born on", "dob", "d.o.b"]
    context_window = 40
    pattern = re.compile(
        r"""\b(?:
              \d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}
            | \d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\,?\s+\d{4}
            | (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}
        )\b""",
        re.VERBOSE | re.IGNORECASE,
    )


# --------------------------------------------------------------------------
# Recognizers -- India-specific extensions
# --------------------------------------------------------------------------

class PANRecognizer(RegexRecognizer):
    """Permanent Account Number: five letters, four digits, one letter."""

    pii_type = PIIType.PAN
    score = 0.97
    pattern = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")


class AadhaarRecognizer(RegexRecognizer):
    pii_type = PIIType.AADHAAR
    score = 0.97
    pattern = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")

    def validate(self, match: re.Match) -> bool:
        return verhoeff_valid(match.group(0))


class DINRecognizer(RegexRecognizer):
    """Director Identification Number -- 8 digits, context-gated.

    Bare 8-digit numbers are common in financial tables, so we only accept
    them near an explicit DIN label or inside a director table row.
    """

    pii_type = PIIType.DIN
    score = 0.85
    context_keywords = ["din", "director identification"]
    context_window = 50
    pattern = re.compile(r"(?<!\d)\d{8}(?!\d)")


class CINRecognizer(RegexRecognizer):
    """Corporate Identity Number, e.g. U28129PN1979PLC141032."""

    pii_type = PIIType.CIN
    score = 0.99
    pattern = re.compile(r"\b[LUu]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")


class BankAccountRecognizer(RegexRecognizer):
    pii_type = PIIType.BANK_ACCOUNT
    score = 0.85
    context_keywords = ["account no", "account number", "a/c no", "a/c number", "account:"]
    context_window = 45
    pattern = re.compile(r"(?<!\d)\d{9,18}(?!\d)")


class WebsiteRecognizer(RegexRecognizer):
    """URLs and bare www hostnames.

    Classed as PII-adjacent: a corporate website identifies the organisation
    just as directly as its name, so redacting names while leaving
    `www.kshinternational.com` intact would defeat the purpose.
    """

    pii_type = PIIType.WEBSITE
    score = 0.9
    pattern = re.compile(
        r"\b(?:https?://|www\.)[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
        re.IGNORECASE,
    )


#: Registry consumed by the pipeline. Order is irrelevant -- overlaps are
#: resolved centrally in `entities.resolve_overlaps`.
REGEX_RECOGNIZERS: List[RegexRecognizer] = [
    EmailRecognizer(),
    PhoneRecognizer(),
    IPAddressRecognizer(),
    SSNRecognizer(),
    CreditCardRecognizer(),
    DateOfBirthRecognizer(),
    PANRecognizer(),
    AadhaarRecognizer(),
    DINRecognizer(),
    CINRecognizer(),
    BankAccountRecognizer(),
    WebsiteRecognizer(),
]
