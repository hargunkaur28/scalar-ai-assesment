"""Optional statistical NER layer (spaCy, or Presidio if available).

This is a *supplement*, not the backbone. The deterministic recognizers above
carry the load; NER is here to catch person and organisation names that never
appear in a high-confidence context anywhere in the document -- a name
mentioned once, in running prose, with no title beside it.

It degrades gracefully: if neither library is installed the pipeline logs a
warning and continues with the deterministic recognizers only. That property
is what makes the tool runnable in a locked-down environment (and it is how
the bundled evaluation run was produced -- see EVALUATION.md).
"""

from __future__ import annotations

import logging
from typing import Iterator, List

from ..entities import Entity, PIIType
from ..gazetteer import LEGISLATION_MARKERS, PUBLIC_BODIES
from .context_recognizers import _is_name_like

logger = logging.getLogger(__name__)

#: spaCy/Presidio label -> our taxonomy.
LABEL_MAP = {
    "PERSON": PIIType.PERSON,
    "PER": PIIType.PERSON,
    "ORG": PIIType.ORGANISATION,
    "ORGANIZATION": PIIType.ORGANISATION,
    "GPE": PIIType.ADDRESS,
    "LOC": PIIType.ADDRESS,
    "FAC": PIIType.ADDRESS,
    "DATE_TIME": PIIType.DATE_OF_BIRTH,
}


class SpacyRecognizer:
    """Thin wrapper over a spaCy pipeline, emitting our `Entity` objects."""

    name = "SpacyRecognizer"

    def __init__(self, model: str = "en_core_web_lg") -> None:
        import spacy  # imported lazily so the package stays optional

        try:
            self.nlp = spacy.load(model, disable=["lemmatizer", "textcat"])
        except OSError:
            logger.warning("spaCy model %s not found; falling back to en_core_web_sm", model)
            self.nlp = spacy.load("en_core_web_sm", disable=["lemmatizer", "textcat"])

    def analyse(self, text: str) -> Iterator[Entity]:
        if not text.strip():
            return
        for ent in self.nlp(text).ents:
            pii_type = LABEL_MAP.get(ent.label_)
            if pii_type is None:
                continue
            if pii_type is PIIType.PERSON and not _is_name_like(ent.text):
                continue
            if pii_type is PIIType.ORGANISATION and not self._org_allowed(ent.text):
                continue
            if pii_type is PIIType.DATE_OF_BIRTH:
                # spaCy's DATE label is far too broad for an offer document;
                # the context-gated regex owns this type instead.
                continue
            yield Entity(
                start=ent.start_char,
                end=ent.end_char,
                pii_type=pii_type,
                # NER is probabilistic, so it scores below the deterministic
                # recognizers and loses overlap ties to them.
                text=ent.text,
                score=0.6,
                source=self.name,
            )

    @staticmethod
    def _org_allowed(text: str) -> bool:
        lowered = text.lower().strip()
        if lowered in PUBLIC_BODIES:
            return False
        return not ({t.strip(",.").lower() for t in text.split()} & LEGISLATION_MARKERS)


def build_ner_recognizers(enabled: bool = True, model: str = "en_core_web_lg") -> List[object]:
    """Return the NER layer if it can be loaded, else an empty list.

    Args:
        enabled: Set False to force the deterministic-only configuration.
        model: spaCy model name to attempt first.

    Returns:
        A single-element list holding the recognizer, or `[]`.
    """
    if not enabled:
        return []
    try:
        return [SpacyRecognizer(model)]
    except ImportError:
        logger.warning(
            "spaCy is not installed -- running with deterministic recognizers only. "
            "Install with: pip install spacy && python -m spacy download en_core_web_lg"
        )
        return []
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("Could not initialise the NER layer (%s); continuing without it.", exc)
        return []
