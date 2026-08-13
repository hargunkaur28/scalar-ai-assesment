"""Surrogate (fake value) generation.

Two properties matter here:

*Consistency* -- the same real value must map to the same fake value
everywhere in the document. If "Kushal Subbayya Hegde" became a different
person on each of his 40 mentions, the redacted prospectus would be
incoherent and useless for the downstream testing it exists to enable.

*Determinism* -- the mapping is derived from a SHA-256 of the normalised
source value plus a salt, so two runs over the same input produce byte-identical
output. That is what makes the evaluation reproducible. Pass a different salt
to get a different, still-consistent, mapping.

Faker is used when installed (for a wider pool of surnames and streets) but is
not required; the built-in pools below keep the tool dependency-light.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Tuple

from .entities import PIIType

FIRST_NAMES = [
    "John", "Peter", "Alice", "Marcus", "Priya", "Daniel", "Sofia", "Arjun",
    "Elena", "Thomas", "Nadia", "Oliver", "Maya", "Victor", "Lena", "Samuel",
    "Iris", "Felix", "Clara", "Hugo", "Ravi", "Anita", "Julian", "Mira",
    "Owen", "Tessa", "Leo", "Nina", "Caleb", "Rosa", "Adrian", "Freya",
]
LAST_NAMES = [
    "Doe", "Parker", "Whitfield", "Ellison", "Marsh", "Okafor", "Rivera",
    "Sandoval", "Bishop", "Castellan", "Novak", "Ashford", "Delacroix",
    "Ferreira", "Halloway", "Ibarra", "Kestrel", "Lindqvist", "Moreau",
    "Norrington", "Ortega", "Pendleton", "Quintero", "Radcliffe", "Sorrell",
    "Thorne", "Underwood", "Valdez", "Wexley", "Yarrow", "Zimmer", "Alcott",
]
COMPANY_HEADS = [
    "Northwind", "Blue Harbor", "Silverline", "Kestrel", "Meridian",
    "Ironwood", "Lakeshore", "Cobalt", "Redstone", "Fairmont", "Applegate",
    "Brightpath", "Cedarfield", "Duskwater", "Everline", "Foxglove",
    "Granite Bay", "Highmark", "Juniper", "Kingsway", "Lanternfish",
]
COMPANY_TAILS = [
    "Industries", "Holdings", "Technologies", "Capital", "Partners",
    "Ventures", "Solutions", "Enterprises", "Group", "Systems",
]
STREETS = [
    "12 Maple Avenue", "48 Riverbend Road", "7 Kingfisher Lane",
    "215 Orchard Street", "90 Fernhill Close", "33 Beacon Way",
    "160 Alder Crescent", "5 Windmill Court", "77 Granary Lane",
]
LOCALITIES = [
    "Sunrise Layout", "Green Meadows", "Lakeview Enclave", "Rosewood Colony",
    "Harbour Gardens", "Silver Oak Estate", "Willow Park", "Cedar Heights",
]
# Deliberately no state or country: an address that straddles a line break is
# redacted only as far as the paragraph runs, so the original "<State>, India"
# tail often survives on the next line. Ending the surrogate at the PIN lets
# that residue read as the natural continuation instead of a contradiction.
CITIES = [
    ("Springfield", "411 001"), ("Fairview", "411 014"), ("Riverton", "410 506"),
    ("Ashgrove", "412 105"), ("Northport", "411 038"), ("Elmwood", "413 102"),
]
DOMAINS = ["example.com", "example.org", "example.net"]


class SurrogateFactory:
    """Maps real PII values to stable fake ones.

    Args:
        salt: Changes the mapping without changing its consistency. Rotate it
            if a redacted document and its mapping table are ever separated.
        keep_mapping: When True, retains a real->fake table for the audit log.
            Treat that table as sensitive: it re-identifies the document.
    """

    def __init__(self, salt: str = "pii-redactor-v1", keep_mapping: bool = True) -> None:
        self.salt = salt
        self.keep_mapping = keep_mapping
        self._cache: Dict[Tuple[str, str], str] = {}
        self.mapping: Dict[str, Dict[str, str]] = {}
        try:
            from faker import Faker  # optional

            self._faker = Faker("en_US")
            self._faker.seed_instance(abs(self._hash(salt)) % (2**31))
        except ImportError:
            self._faker = None

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _normalise(value: str) -> str:
        return " ".join(value.split()).strip().lower()

    def _hash(self, value: str) -> int:
        digest = hashlib.sha256(f"{self.salt}|{value}".encode("utf-8")).hexdigest()
        return int(digest[:16], 16)

    def _pick(self, pool: List, value: str, offset: int = 0) -> object:
        return pool[(self._hash(value) + offset * 7919) % len(pool)]

    # -- public API ---------------------------------------------------------

    def get(self, pii_type: PIIType, value: str) -> str:
        """Return the stable surrogate for `value`, generating it on first use."""
        key = (pii_type.value, self._normalise(value))
        if key not in self._cache:
            surrogate = self._generate(pii_type, self._normalise(value), value)
            self._cache[key] = surrogate
            if self.keep_mapping:
                self.mapping.setdefault(pii_type.value, {})[value] = surrogate
        return self._match_case(self._cache[key], value)

    @staticmethod
    def _match_case(surrogate: str, original: str) -> str:
        """Mirror ALL-CAPS originals so cover pages keep their typography."""
        letters = [c for c in original if c.isalpha()]
        if letters and all(c.isupper() for c in letters):
            return surrogate.upper()
        return surrogate

    # -- per-type generators ------------------------------------------------

    def _generate(self, pii_type: PIIType, norm: str, raw: str) -> str:
        handler = {
            PIIType.PERSON: self._person,
            PIIType.EMAIL: self._email,
            PIIType.PHONE: self._phone,
            PIIType.ORGANISATION: self._organisation,
            PIIType.ADDRESS: self._address,
            PIIType.SSN: self._ssn,
            PIIType.CREDIT_CARD: self._credit_card,
            PIIType.DATE_OF_BIRTH: self._dob,
            PIIType.IP_ADDRESS: self._ip,
            PIIType.PAN: self._pan,
            PIIType.AADHAAR: self._aadhaar,
            PIIType.DIN: self._din,
            PIIType.CIN: self._cin,
            PIIType.BANK_ACCOUNT: self._bank_account,
            PIIType.WEBSITE: self._website,
        }.get(pii_type)
        return handler(norm, raw) if handler else "[REDACTED]"

    def _person(self, norm: str, raw: str) -> str:
        # Preserve the token count so "Mr. Hegde" doesn't become a full name.
        token_count = len(norm.split())
        first = self._pick(FIRST_NAMES, norm)
        last = self._pick(LAST_NAMES, norm, 1)
        if token_count == 1:
            return str(last)
        if token_count >= 3:
            middle = self._pick(LAST_NAMES, norm, 2)
            return f"{first} {middle} {last}"
        return f"{first} {last}"

    def _email(self, norm: str, raw: str) -> str:
        local, _, _domain = norm.partition("@")
        person = self._person(local.replace(".", " "), local)
        handle = ".".join(person.lower().split())
        return f"{handle}@{self._pick(DOMAINS, norm)}"

    def _phone(self, norm: str, raw: str) -> str:
        """Keep the country code, digit count and separators; change the digits."""
        digits = [c for c in raw if c.isdigit()]
        seed = self._hash(norm)
        replacement = []
        for index in range(len(digits)):
            replacement.append(str((seed >> (index * 3)) % 10))
        # Preserve a leading +91 (or whatever code) so format checks still pass.
        if raw.lstrip().startswith("+") and len(replacement) > 2:
            replacement[0], replacement[1] = digits[0], digits[1]
        out, cursor = [], 0
        for char in raw:
            if char.isdigit():
                out.append(replacement[cursor])
                cursor += 1
            else:
                out.append(char)
        return "".join(out)

    def _organisation(self, norm: str, raw: str) -> str:
        head = self._pick(COMPANY_HEADS, norm)
        tail = self._pick(COMPANY_TAILS, norm, 1)
        suffix = ""
        for candidate in (" private limited", " limited", " llp", " ltd", " inc", " trust", " bank"):
            if norm.endswith(candidate):
                suffix = candidate.title().replace("Llp", "LLP")
                break
        return f"{head} {tail}{suffix}"

    def _address(self, norm: str, raw: str) -> str:
        street = self._pick(STREETS, norm)
        locality = self._pick(LOCALITIES, norm, 1)
        city, pin = self._pick(CITIES, norm, 2)
        return f"{street}, {locality}, {city} - {pin}"

    def _ssn(self, norm: str, raw: str) -> str:
        seed = self._hash(norm)
        area = 100 + seed % 800
        group = 1 + (seed >> 10) % 99
        serial = 1 + (seed >> 20) % 9999
        return f"{area:03d}-{group:02d}-{serial:04d}"

    def _credit_card(self, norm: str, raw: str) -> str:
        """Emit a Luhn-valid test card so downstream validators still pass."""
        seed = self._hash(norm)
        body = "4" + "".join(str((seed >> (i * 3)) % 10) for i in range(14))
        total, parity = 0, (len(body) + 1) % 2
        for index, char in enumerate(body):
            digit = int(char)
            if index % 2 == parity:
                digit *= 2
                if digit > 9:
                    digit -= 9
            total += digit
        number = body + str((10 - total % 10) % 10)
        return f"{number[:4]} {number[4:8]} {number[8:12]} {number[12:]}"

    def _dob(self, norm: str, raw: str) -> str:
        seed = self._hash(norm)
        year = 1955 + seed % 45
        month = 1 + (seed >> 8) % 12
        day = 1 + (seed >> 16) % 28
        if "/" in raw:
            return f"{day:02d}/{month:02d}/{year}"
        if "-" in raw:
            return f"{day:02d}-{month:02d}-{year}"
        months = ["January", "February", "March", "April", "May", "June", "July",
                  "August", "September", "October", "November", "December"]
        return f"{months[month - 1]} {day}, {year}"

    def _ip(self, norm: str, raw: str) -> str:
        if ":" in raw:
            seed = self._hash(norm)
            groups = [f"{(seed >> (i * 8)) % 65536:04x}" for i in range(4)]
            return "2001:db8:" + ":".join(groups)
        seed = self._hash(norm)
        # 198.51.100.0/24 is TEST-NET-2, reserved for documentation (RFC 5737).
        return f"198.51.100.{seed % 254 + 1}"

    def _pan(self, norm: str, raw: str) -> str:
        seed = self._hash(norm)
        alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        head = "".join(alpha[(seed >> (i * 5)) % 26] for i in range(5))
        digits = f"{(seed >> 25) % 10000:04d}"
        return f"{head}{digits}{alpha[(seed >> 40) % 26]}"

    def _aadhaar(self, norm: str, raw: str) -> str:
        seed = self._hash(norm)
        return f"{2000 + seed % 8000} {(seed >> 12) % 9000 + 1000} {(seed >> 24) % 9000 + 1000}"

    def _din(self, norm: str, raw: str) -> str:
        return f"{self._hash(norm) % 90000000 + 10000000:08d}"

    def _cin(self, norm: str, raw: str) -> str:
        seed = self._hash(norm)
        return f"U{(seed % 90000) + 10000:05d}XX{1980 + seed % 40}PLC{(seed >> 8) % 900000 + 100000:06d}"

    def _bank_account(self, norm: str, raw: str) -> str:
        digits = len([c for c in raw if c.isdigit()])
        seed = self._hash(norm)
        return "".join(str((seed >> (i * 3)) % 10) for i in range(digits))

    def _website(self, norm: str, raw: str) -> str:
        head = str(self._pick(COMPANY_HEADS, norm)).lower().replace(" ", "")
        scheme = "https://" if raw.lower().startswith("http") else "www."
        if scheme == "https://":
            return f"https://www.{head}-example.com"
        return f"www.{head}-example.com"
