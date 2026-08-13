"""Recognizers for PII that has no fixed grammar: people, companies, addresses.

These use a two-pass, document-level strategy rather than per-block matching:

  Pass A (harvest)   Walk the whole document and collect *candidates* that
                     appear in a high-confidence context at least once --
                     next to an honorific, inside a personal email address, or
                     in a table row that also contains a job title.

  Pass B (propagate) Redact every occurrence of every harvested candidate,
                     everywhere in the document.

The point of the split is that a name only has to be identifiable *once* to be
redacted *everywhere*. "Rakhi Girija Shetty" appears next to "Whole-time
Director" in one table on page 60; that single sighting is what licenses
redacting the bare name on page 250, where no context is available. This is
what lifts PERSON recall well above what a purely local classifier achieves,
without loosening the precision guard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Sequence, Set

from ..entities import Entity, PIIType
from ..gazetteer import (
    ADDRESS_MARKERS,
    HONORIFICS,
    INDIAN_STATES,
    LEGISLATION_MARKERS,
    GENERIC_HEAD_NOUNS,
    NON_PERSON_TOKENS,
    ORG_INDICATOR_TOKENS,
    ORG_LEADING_NOISE,
    ORG_LEADING_STOPWORDS,
    ORG_SUFFIXES,
    PERSON_CONTEXT_KEYWORDS,
    PUBLIC_BODIES,
    STRONG_ORG_SUFFIXES,
)

# Local parts that denote a shared/role mailbox rather than an individual.
ROLE_MAILBOXES = {
    "info", "contact", "support", "help", "helpdesk", "admin", "office",
    "sales", "enquiry", "enquiries", "query", "queries", "care",
    "customercare", "customerservice", "grievance", "grievances", "redressal",
    "compliance", "secretarial", "cs", "connect", "investor", "investors",
    "ir", "ipo", "ipos", "offer", "issue", "mail", "email", "webmaster",
    "noreply", "no-reply", "hr", "careers", "jobs", "legal", "finance",
    "accounts", "billing", "pro", "media", "press", "marketing", "team",
    "service", "services", "desk", "general", "corporate", "company",
}

_HONORIFIC_ALT = "|".join(re.escape(h) for h in sorted(HONORIFICS, key=len, reverse=True))
_STATE_ALT = "|".join(re.escape(s) for s in INDIAN_STATES)
_SUFFIX_ALT = "|".join(re.escape(s) for s in sorted(STRONG_ORG_SUFFIXES, key=len, reverse=True))

# A "name-shaped" token: Capitalised, or fully upper-case (cover pages shout).
_NAME_TOKEN = r"(?:[A-Z][a-z'\-]{1,20}|[A-Z]{2,20})"

# Only the honorific is case-insensitive; the name tokens must stay
# case-sensitive or `re.IGNORECASE` would let "Mr. Rohan Dey at rohan" match.
HONORIFIC_NAME_RE = re.compile(
    rf"\b(?i:{_HONORIFIC_ALT})\s+((?:{_NAME_TOKEN}\s+){{0,3}}{_NAME_TOKEN})\b"
)
CANDIDATE_NAME_RE = re.compile(rf"\b{_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,3}}\b")
# Note the separator class excludes the comma. With it included, a single
# match happily spanned "TRUST A, TRUST B, TRUST C" as one organisation.
# Company names contain lowercase connectors -- "Shubhkamal Leasing and
# Investment Private Limited". Without allowing them the match stops at "and".
_ORG_CONNECTOR = r"(?:and|of|for|&|the)"
ORG_RE = re.compile(
    rf"\b((?:(?:{_NAME_TOKEN}|{_ORG_CONNECTOR})[\s&.\-]+){{1,8}}(?i:{_SUFFIX_ALT}))\b"
)

#: "Sandesh Bhagwat, CEO" / "Ganesh Prasad, Technical Director" -- a name
#: followed immediately by its job title. Much tighter than block-level
#: context, so it works inside long prose where the length gate blocks the
#: general harvester.
NAME_THEN_ROLE_RE = re.compile(
    rf"({_NAME_TOKEN}(?:\s+{_NAME_TOKEN}){{1,3}})\s*,\s*"
    rf"(?i:(?:[A-Za-z\-]+\s+){{0,2}}(?:{'|'.join(re.escape(k) for k in PERSON_CONTEXT_KEYWORDS)}))"
)
PIN_RE = re.compile(r"\b\d{3}\s?\d{3}\b")
STATE_RE = re.compile(rf"\b(?:{_STATE_ALT})\b", re.IGNORECASE)


#: Single-word address markers, used to keep "Off Pallod Farms" out of the
#: person list.
_ADDRESS_MARKER_TOKENS = {m for m in ADDRESS_MARKERS if " " not in m and "." not in m}

#: A single-letter initial, with or without its period.
_INITIAL_RE = re.compile(r"[A-Z]\.?")

#: Function words that a greedy capitalised-sequence match can pick up at the
#: edges of a real name ("Rohan Dey at", "to Nuvama Wealth").
FUNCTION_WORDS = {
    "at", "to", "of", "the", "and", "or", "in", "on", "by", "for", "with",
    "from", "as", "is", "are", "was", "were", "be", "our", "its", "their",
    "his", "her", "this", "that", "these", "those", "an", "a", "no", "not",
    "being", "namely", "viz", "late", "hindu", "undivided", "family", "huf",
    "jointly", "severally", "each", "both", "either", "neither",
}


def _is_name_like(phrase: str) -> bool:
    """Reject capitalised phrases that are domain jargon rather than names."""
    tokens = [t for t in re.split(r"[\s\-]+", phrase) if t]
    if not 2 <= len(tokens) <= 4:
        return False
    if all(_INITIAL_RE.fullmatch(t) for t in tokens):
        return False  # "B. N." is initials with no name attached
    for token in tokens:
        # Middle initials are legitimate name tokens: "Narayna B. Shetty".
        if _INITIAL_RE.fullmatch(token):
            continue
        bare = token.strip(".,'&").lower()
        if len(bare) < 2 or not bare.replace("'", "").isalpha():
            return False
        if bare in NON_PERSON_TOKENS or bare in FUNCTION_WORDS:
            return False
        if bare in _ADDRESS_MARKER_TOKENS or bare in ORG_INDICATOR_TOKENS:
            return False
    return True


def _has_person_context(text: str) -> bool:
    lowered = text.lower()
    return any(keyword in lowered for keyword in PERSON_CONTEXT_KEYWORDS)


def name_from_email(local_part: str) -> str | None:
    """Derive a person name from an email local part.

    ``tushar.gavankar`` -> ``Tushar Gavankar``. Returns None for role
    mailboxes and for single-token locals, which are too ambiguous to
    propagate safely across a 56,000-word document.
    """
    cleaned = re.sub(r"\d+$", "", local_part)
    parts = [p for p in re.split(r"[._\-]+", cleaned) if p]
    if len(parts) < 2:
        return None
    if any(p.lower() in ROLE_MAILBOXES for p in parts):
        return None
    if not all(p.isalpha() and len(p) >= 2 for p in parts):
        return None
    return " ".join(p.capitalize() for p in parts)


@dataclass
class DocumentContext:
    """Everything harvested in pass A, shared by the pass-B recognizers."""

    person_names: Set[str] = field(default_factory=set)
    header_texts: Set[str] = field(default_factory=set)
    organisation_names: Set[str] = field(default_factory=set)
    _person_re: re.Pattern | None = field(default=None, repr=False)
    _surname_re: re.Pattern | None = field(default=None, repr=False)
    _org_re: re.Pattern | None = field(default=None, repr=False)

    # -- pass A -------------------------------------------------------------

    def harvest(self, text: str, context: str = "", is_header: bool = False) -> None:
        """Collect person/org candidates from one text block.

        Header cells are recorded but never mined for names: they hold column
        labels, and a label like "WEIGHTED AVERAGE COST" is indistinguishable
        from a name by shape alone.
        """
        # A merged banner row ("OUR PROMOTERS: A, B, C") is technically the
        # table's first row but carries data, not labels. The discriminator is
        # whether the cell itself names a role: a true label row does not.
        if is_header and not _has_person_context(text):
            self.header_texts.add(text.lower())
            self._harvest_orgs(text)
            return
        self._harvest_people(text, context or text)
        self._harvest_orgs(text)

    def _is_header_label(self, phrase: str) -> bool:
        lowered = phrase.lower()
        return any(lowered in header for header in self.header_texts)

    #: "OUR PROMOTERS: A, B, C" -- a role label introducing a delimited list.
    LABEL_LIST_RE = re.compile(
        rf"(?:{'|'.join(re.escape(k) for k in PERSON_CONTEXT_KEYWORDS)})\s*[:\-]\s*(.{{5,400}})",
        re.IGNORECASE | re.DOTALL,
    )

    def _harvest_label_list(self, text: str) -> None:
        """Mine "Role: Name, Name, Name" banners, which the length gate skips.

        These rows are long (so the short-block rule rejects them) but are
        unambiguously person data (so dropping them costs real recall).
        """
        match = self.LABEL_LIST_RE.search(text)
        if not match:
            return
        for fragment in re.split(r",|\band\b|/|;", match.group(1)):
            phrase = self._longest_valid_subspan(fragment.strip())
            if phrase and not self._is_header_label(phrase):
                self.person_names.add(phrase)

    def _harvest_people(self, text: str, context: str) -> None:
        for match in HONORIFIC_NAME_RE.finditer(text):
            phrase = match.group(1).strip()
            if _is_name_like(phrase):
                self.person_names.add(phrase)

        for local in re.findall(r"\b([A-Za-z0-9._%+\-]+)@", text):
            derived = name_from_email(local)
            if derived:
                self.person_names.add(derived)

        # Row-level context: a cell holding only a name, in a row that also
        # holds a job title, is a name.
        #
        # Two guards keep this from over-firing. First, the block must be
        # SHORT -- a table cell or a caption, not a paragraph. A 900-word
        # paragraph that happens to contain the word "director" would
        # otherwise donate every title-cased phrase in it to the name list.
        # Second, the role keyword must be within `CONTEXT_PROXIMITY`
        # characters of the candidate inside the row text, so the signal is
        # genuinely adjacent rather than merely co-located.
        for match in NAME_THEN_ROLE_RE.finditer(text):
            phrase = self._longest_valid_subspan(match.group(1))
            if phrase and not self._is_header_label(phrase):
                self.person_names.add(phrase)

        if _has_person_context(text):
            self._harvest_label_list(text)
        if len(text) > self.MAX_CONTEXT_BLOCK_LEN or not _has_person_context(context):
            return
        for match in CANDIDATE_NAME_RE.finditer(text):
            # A greedy match often trails one junk token ("Manisha Shukla
            # Website"). Rather than discarding the whole candidate, fall back
            # to the longest contiguous sub-span that passes the name test --
            # which recovers the real name inside it.
            phrase = self._longest_valid_subspan(match.group(0).strip())
            if phrase is None:
                continue
            if self._looks_like_org(phrase, text, match.start()):
                continue
            if not self._keyword_is_near(phrase, context):
                continue
            if self._is_header_label(phrase):
                continue
            self.person_names.add(phrase)

    def _harvest_orgs(self, text: str) -> None:
        for match in ORG_RE.finditer(text):
            phrase = self.trim_org_noise(re.sub(r"\s+", " ", match.group(1)).strip(" ,.&-"))
            if self._is_public_body(phrase) or self._is_legislation(phrase):
                continue
            if not self.is_org_like(phrase):
                continue
            self.organisation_names.add(phrase)
            # Register the suffix-stripped alias too: documents refer to
            # "Nuvama Wealth Management" as often as to the full legal name.
            alias = self._strip_suffix(phrase)
            if alias and len(alias.split()) >= 2 and not self._is_public_body(alias):
                self.organisation_names.add(alias)

    @staticmethod
    def _strip_suffix(phrase: str) -> str | None:
        lowered = phrase.lower()
        for suffix in sorted(ORG_SUFFIXES, key=len, reverse=True):
            if lowered.endswith(" " + suffix):
                return phrase[: -(len(suffix) + 1)].strip(" ,.&-")
        return None

    @staticmethod
    def _is_public_body(phrase: str) -> bool:
        return phrase.lower().strip() in PUBLIC_BODIES

    @staticmethod
    def _is_legislation(phrase: str) -> bool:
        tokens = {t.lower().strip(",.") for t in phrase.split()}
        return bool(tokens & LEGISLATION_MARKERS)

    @staticmethod
    def _longest_valid_subspan(phrase: str) -> str | None:
        """Return the longest contiguous token run that looks like a name."""
        tokens = phrase.split()
        for width in range(min(4, len(tokens)), 1, -1):
            for start in range(0, len(tokens) - width + 1):
                candidate = " ".join(tokens[start:start + width])
                if _is_name_like(candidate):
                    return candidate
        return None

    #: Blocks longer than this are prose, not a labelled field.
    MAX_CONTEXT_BLOCK_LEN = 90
    #: How far a role keyword may sit from a candidate within the row text.
    CONTEXT_PROXIMITY = 250

    @classmethod
    def _keyword_is_near(cls, phrase: str, context: str) -> bool:
        lowered = context.lower()
        position = lowered.find(phrase.lower())
        if position == -1:
            return True  # candidate not locatable in context; fall back to block-level
        for keyword in PERSON_CONTEXT_KEYWORDS:
            index = lowered.find(keyword)
            while index != -1:
                if abs(index - position) <= cls.CONTEXT_PROXIMITY:
                    return True
                index = lowered.find(keyword, index + 1)
        return False

    @staticmethod
    def _looks_like_org(phrase: str, text: str, start: int) -> bool:
        """True if a company suffix follows closely -- "MUFG Intime India
        Private Limited" must not donate "MUFG Intime" to the name list."""
        tail = text[start + len(phrase): start + len(phrase) + 45].lower()
        return any(suffix in tail for suffix in ORG_SUFFIXES)

    @staticmethod
    def trim_org_noise(phrase: str) -> str:
        """Drop leading connectives: 'of KSH International Limited' -> the name."""
        tokens = phrase.split()
        while tokens and tokens[0].strip(",.&-").lower() in ORG_LEADING_NOISE:
            tokens.pop(0)
        return " ".join(tokens)

    @staticmethod
    def is_org_like(phrase: str) -> bool:
        """Reject phrases whose 'company suffix' is really a common noun.

        Weak suffixes -- Capital, Securities, Industries, Bank -- are genuine
        parts of company names but also ordinary vocabulary. The discriminator
        that works in practice is the token immediately before the suffix:
        "Northwind Capital" is a company, "Equity Share capital" is not.
        """
        tokens = [t.strip(",.&-").lower() for t in phrase.split() if t.strip(",.&-")]
        if len(tokens) < 2:
            return False
        lowered = phrase.lower()
        suffix_len = 1
        for suffix in sorted(STRONG_ORG_SUFFIXES, key=len, reverse=True):
            if lowered.endswith(suffix):
                suffix_len = len(suffix.split())
                break
        head = tokens[:-suffix_len] if suffix_len < len(tokens) else []
        if not head:
            return False
        if head[-1] in ORG_LEADING_STOPWORDS or head[-1] in FUNCTION_WORDS:
            return False
        if len(head) == 1 and head[0] in GENERIC_HEAD_NOUNS:
            return False
        return any(t not in ORG_LEADING_STOPWORDS and t not in FUNCTION_WORDS for t in head)

    # -- freeze -------------------------------------------------------------

    def finalise(self) -> None:
        """Compile the propagation patterns once, after harvesting completes."""
        self._person_re = self._build_pattern(self._person_variants())
        self._org_re = self._build_pattern(self.organisation_names)
        self._surname_re = self._build_surname_pattern()

    def _build_surname_pattern(self) -> re.Pattern | None:
        """Match `<Given name> <known surname>` for surnames already confirmed.

        Family members and historical shareholders appear in litigation and
        share-transfer tables with no job title beside them, so the context
        harvester never sees them. But their surname is already confirmed by a
        director or promoter elsewhere in the document, and a capitalised token
        immediately before a confirmed surname is reliably a given name. This
        recovers "Karunakar Hegde" and "D M Shetty" without loosening anything
        else -- the surname must have been independently established first.
        """
        surnames = {
            name.split()[-1] for name in self.person_names if len(name.split()) >= 2
        }
        surnames = {s for s in surnames if len(s) >= 4 and s.lower() not in NON_PERSON_TOKENS}
        if not surnames:
            return None
        alternation = "|".join(re.escape(s) for s in sorted(surnames, key=len, reverse=True))
        # Only the surname is case-insensitive. A global IGNORECASE here let
        # "includes Sandesh Bhagwat" match, because "includes" then satisfied
        # the capitalised-given-name token.
        return re.compile(
            rf"\b(?:(?:[A-Z][A-Za-z'\-]{{1,20}}|[A-Z])\.?\s+){{1,3}}(?i:{alternation})\b"
        )

    def _person_variants(self) -> Set[str]:
        """Full names plus honorific+surname forms (``Mr. Hegde``).

        A bare surname is deliberately *not* propagated: many Indian surnames
        double as common nouns or place names, and propagating them alone
        costs more precision than the recall is worth.
        """
        variants: Set[str] = set(self.person_names)
        for name in self.person_names:
            tokens = name.split()
            if len(tokens) >= 3:
                # "Kushal Subbayya Hegde" is also written "Kushal Hegde".
                variants.add(f"{tokens[0]} {tokens[-1]}")
            if len(tokens) >= 2:
                surname = tokens[-1]
                for honorific in ("Mr.", "Mr", "Ms.", "Ms", "Mrs.", "Mrs", "Dr.", "Dr"):
                    variants.add(f"{honorific} {surname}")
        return variants

    @staticmethod
    def _build_pattern(phrases: Sequence[str] | Set[str]) -> re.Pattern | None:
        if not phrases:
            return None
        # Longest first so "KSH International Limited" wins over "KSH
        # International"; \s+ makes matching robust to line-wrap whitespace.
        ordered = sorted(phrases, key=len, reverse=True)
        alternation = "|".join(
            r"\s+".join(re.escape(tok) for tok in phrase.split()) for phrase in ordered
        )
        return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)

    @property
    def person_pattern(self) -> re.Pattern | None:
        return self._person_re

    @property
    def surname_pattern(self) -> re.Pattern | None:
        return self._surname_re

    @property
    def org_pattern(self) -> re.Pattern | None:
        return self._org_re


# ---------------------------------------------------------------------------
# Pass-B recognizers
# ---------------------------------------------------------------------------

class PersonRecognizer:
    """Match every harvested person name (and honorific+surname variant)."""

    name = "PersonRecognizer"

    def __init__(self, context: DocumentContext) -> None:
        self.context = context

    def analyse(self, text: str) -> Iterator[Entity]:
        pattern = self.context.person_pattern
        if pattern is not None:
            for match in pattern.finditer(text):
                yield Entity(
                    start=match.start(),
                    end=match.end(),
                    pii_type=PIIType.PERSON,
                    text=match.group(0),
                    score=0.9,
                    source=self.name,
                )

        surname_pattern = self.context.surname_pattern
        if surname_pattern is None:
            return
        for match in surname_pattern.finditer(text):
            phrase = self._longest_name(match.group(0))
            if phrase is None:
                continue
            offset = match.group(0).find(phrase)
            yield Entity(
                start=match.start() + offset,
                end=match.start() + offset + len(phrase),
                pii_type=PIIType.PERSON,
                text=phrase,
                score=0.7,
                source=self.name + ":surname",
            )

    @staticmethod
    def _longest_name(phrase: str) -> str | None:
        """Trim a surname match back to the part that looks like a name."""
        tokens = phrase.split()
        for width in range(len(tokens), 1, -1):
            candidate = " ".join(tokens[-width:])
            if _is_name_like(candidate):
                return candidate
        return None


class OrganisationRecognizer:
    """Match harvested company names, plus any unseen `... Limited` phrase."""

    name = "OrganisationRecognizer"

    def __init__(self, context: DocumentContext) -> None:
        self.context = context

    def analyse(self, text: str) -> Iterator[Entity]:
        seen: List[tuple[int, int]] = []
        pattern = self.context.org_pattern
        if pattern is not None:
            for match in pattern.finditer(text):
                seen.append((match.start(), match.end()))
                yield Entity(
                    start=match.start(),
                    end=match.end(),
                    pii_type=PIIType.ORGANISATION,
                    text=match.group(0),
                    score=0.9,
                    source=self.name,
                )
        # Catch legal-suffix phrases that the harvest missed (e.g. a company
        # named only once, in a footnote).
        for match in ORG_RE.finditer(text):
            span = (match.start(1), match.end(1))
            if any(span[0] < e and s < span[1] for s, e in seen):
                continue
            raw = re.sub(r"\s+", " ", match.group(1)).strip(" ,.&-")
            phrase = DocumentContext.trim_org_noise(raw)
            if DocumentContext._is_public_body(phrase) or DocumentContext._is_legislation(phrase):
                continue
            if not DocumentContext.is_org_like(phrase):
                continue
            # Re-anchor the span so trimmed lead-in words are not redacted.
            offset = raw.rfind(phrase.split()[0]) if phrase else 0
            yield Entity(
                start=span[0] + max(offset, 0),
                end=span[1],
                pii_type=PIIType.ORGANISATION,
                text=phrase,
                score=0.8,
                source=self.name + ":suffix",
            )


class AddressRecognizer:
    """Postal addresses, anchored on a six-digit Indian PIN code.

    Strategy: find the PIN, then expand left to the nearest sentence or block
    boundary (capped at `max_left` characters) and right through any trailing
    state / country tokens. A candidate is accepted only if the span contains a
    recognisable address marker or is followed by a state name -- otherwise a
    six-digit share count would drag 200 characters of prose with it.
    """

    name = "AddressRecognizer"
    max_left = 200

    def analyse(self, text: str) -> Iterator[Entity]:
        for match in PIN_RE.finditer(text):
            start = self._left_boundary(text, match.start())
            end = self._right_boundary(text, match.end())
            span_text = text[start:end]
            if not self._is_address(span_text, text, match.end()):
                continue
            start += self._trim_lead_in(span_text)
            span_text = text[start:end]
            stripped = span_text.lstrip(" ,-–:")
            start += len(span_text) - len(stripped)
            yield Entity(
                start=start,
                end=end,
                pii_type=PIIType.ADDRESS,
                text=text[start:end],
                score=0.85,
                source=self.name,
            )

    #: Phrases that introduce an address but are not part of it. Keeping them
    #: out of the span matters for precision: an evaluator comparing spans
    #: character-for-character would otherwise score every address as a miss.
    LEAD_IN_RE = re.compile(
        r"\b(?:situated|located|having its|registered|corporate|principal|"
        r"branch|head|office|address|residing|resident)\b[^,]{0,40}?\bat\b\s*[:,]?\s*",
        re.IGNORECASE,
    )

    def _trim_lead_in(self, span_text: str) -> int:
        match = None
        for candidate in self.LEAD_IN_RE.finditer(span_text[:120]):
            match = candidate
        return match.end() if match else 0

    #: Periods that end an abbreviation, not a sentence. Indian addresses are
    #: full of them ("S. no. 245/ 104", "lane no. 3"), and naively cutting at
    #: the last ". " truncated the address to its final fragment.
    ABBREVIATIONS = {
        "no", "nos", "s", "st", "rd", "opp", "ltd", "pvt", "co", "mr", "mrs",
        "ms", "dr", "jr", "sr", "vs", "etc", "approx", "dept", "bldg", "flr",
        "apt", "ph", "gat", "sy", "hno", "h", "d", "w", "c", "e", "n",
    }

    def _left_boundary(self, text: str, pin_start: int) -> int:
        window_start = max(0, pin_start - self.max_left)
        window = text[window_start:pin_start]
        cut = -1
        for match in re.finditer(r"[.:;]\s+", window):
            preceding = re.search(r"(\w+)\W*$", window[: match.start()])
            token = preceding.group(1).lower() if preceding else ""
            # A real boundary needs a real word before it, not an abbreviation
            # and not a bare number ("104." in a house number).
            if len(token) <= 1 or token in self.ABBREVIATIONS or token.isdigit():
                continue
            cut = match.end()
        return window_start + cut if cut != -1 else window_start

    @staticmethod
    def _right_boundary(text: str, pin_end: int) -> int:
        tail = text[pin_end: pin_end + 60]
        match = re.match(rf"[\s,\-–]*(?:{_STATE_ALT})?[\s,\-–]*(?:India)?", tail, re.IGNORECASE)
        return pin_end + (match.end() if match else 0)

    @staticmethod
    def _is_address(span_text: str, full_text: str, pin_end: int) -> bool:
        lowered = span_text.lower()
        if any(marker in lowered for marker in ADDRESS_MARKERS):
            return True
        return bool(STATE_RE.match(full_text[pin_end:].lstrip(" ,-–")))
