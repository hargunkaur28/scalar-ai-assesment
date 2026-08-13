"""The orchestration layer: harvest -> detect -> resolve -> replace.

`RedactionPipeline` is the only class most callers need. It is deliberately
agnostic about where text comes from, so the same engine backs the CLI, the
web app, and the evaluation harness.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from docx import Document

from .docx_io import (
    TextBlock,
    apply_replacements,
    iter_text_blocks,
    scrub_core_properties,
)
from .entities import Entity, PIIType, RedactionStats, resolve_overlaps
from .recognizers.context_recognizers import (
    AddressRecognizer,
    DocumentContext,
    OrganisationRecognizer,
    PersonRecognizer,
)
from .recognizers.ner import build_ner_recognizers
from .recognizers.regex_recognizers import REGEX_RECOGNIZERS
from .surrogates import SurrogateFactory

logger = logging.getLogger(__name__)


@dataclass
class RedactionConfig:
    """Knobs a reviewer might reasonably want to turn.

    Attributes:
        enabled_types: Restrict redaction to these types. None = all.
        use_ner: Attempt to load the optional spaCy layer.
        ner_model: spaCy model to try first.
        salt: Surrogate mapping salt (see SurrogateFactory).
        scrub_metadata: Also blank the docx core properties.
        emit_mapping: Keep the real->fake table for the audit log.
    """

    enabled_types: Optional[Set[PIIType]] = None
    use_ner: bool = True
    ner_model: str = "en_core_web_lg"
    salt: str = "pii-redactor-v1"
    scrub_metadata: bool = True
    emit_mapping: bool = True


@dataclass
class RedactionResult:
    """Everything a caller needs to report on, or evaluate, a run."""

    stats: RedactionStats
    entities: List[Tuple[str, Entity]] = field(default_factory=list)
    mapping: Dict[str, Dict[str, str]] = field(default_factory=dict)
    blocks_processed: int = 0
    metadata_fields_scrubbed: List[str] = field(default_factory=list)
    ner_active: bool = False

    def to_dict(self, include_mapping: bool = False) -> dict:
        payload = {
            "blocks_processed": self.blocks_processed,
            "entities_redacted": self.stats.total(),
            "counts_by_type": self.stats.counts,
            "metadata_fields_scrubbed": self.metadata_fields_scrubbed,
            "ner_active": self.ner_active,
        }
        if include_mapping:
            payload["mapping"] = self.mapping
        return payload


class RedactionPipeline:
    """Detect and pseudonymise PII across a whole document.

    Usage:
        >>> pipeline = RedactionPipeline(RedactionConfig())
        >>> result = pipeline.redact_docx("in.docx", "out.docx")
        >>> result.stats.counts
    """

    def __init__(self, config: Optional[RedactionConfig] = None) -> None:
        self.config = config or RedactionConfig()
        self.surrogates = SurrogateFactory(
            salt=self.config.salt, keep_mapping=self.config.emit_mapping
        )
        self.context = DocumentContext()
        self._ner = build_ner_recognizers(self.config.use_ner, self.config.ner_model)
        self._recognizers: List[object] = []

    # -- construction -------------------------------------------------------

    def _build_recognizers(self) -> None:
        self._recognizers = [
            *REGEX_RECOGNIZERS,
            PersonRecognizer(self.context),
            OrganisationRecognizer(self.context),
            AddressRecognizer(),
            *self._ner,
        ]

    def _type_enabled(self, pii_type: PIIType) -> bool:
        return self.config.enabled_types is None or pii_type in self.config.enabled_types

    # -- core -------------------------------------------------------------

    def harvest(self, blocks: Sequence[TextBlock]) -> None:
        """Pass A -- learn which names and companies this document contains."""
        # Two sweeps: headers first, so that column labels are already known
        # to be labels by the time body cells are mined for names.
        for block in blocks:
            if block.is_header:
                self.context.harvest(block.text, block.context, is_header=True)
        for block in blocks:
            if not block.is_header:
                self.context.harvest(block.text, block.context)
        self.context.finalise()
        self._build_recognizers()
        logger.info(
            "Harvested %d person names and %d organisation names",
            len(self.context.person_names),
            len(self.context.organisation_names),
        )

    def detect(self, text: str) -> List[Entity]:
        """Run every recognizer over one block and return non-overlapping spans."""
        found: List[Entity] = []
        for recognizer in self._recognizers:
            try:
                for entity in recognizer.analyse(text):
                    if self._type_enabled(entity.pii_type):
                        found.append(entity)
            except Exception as exc:  # a bad pattern must not kill the run
                logger.warning("Recognizer %s failed: %s", type(recognizer).__name__, exc)
        return resolve_overlaps(found)

    def harvest_text(self, text: str, context: str = "") -> None:
        """Harvest from a bare string. Convenience wrapper for tests/eval."""
        self.context.harvest(text, context)
        self.context.finalise()
        self._build_recognizers()

    def redact_text(self, text: str, harvest: bool = True) -> Tuple[str, List[Entity]]:
        """Convenience path for plain strings (used by tests and the eval).

        Args:
            text: The string to redact.
            harvest: Run pass A over this string first. Set False when the
                pipeline has already harvested a whole document, so that
                document-wide knowledge is used instead.
        """
        if harvest:
            self.harvest_text(text)
        elif not self._recognizers:
            self._build_recognizers()
        entities = self.detect(text)
        out, cursor = [], 0
        for entity in entities:
            out.append(text[cursor:entity.start])
            out.append(self.surrogates.get(entity.pii_type, entity.text))
            cursor = entity.end
        out.append(text[cursor:])
        return "".join(out), entities

    # -- document entry point ----------------------------------------------

    def redact_docx(self, source: str | Path, destination: str | Path) -> RedactionResult:
        """Redact `source` and write the result to `destination`.

        Args:
            source: Path to the input .docx.
            destination: Path to write the redacted .docx to.

        Returns:
            A `RedactionResult` with counts, per-entity detail and the
            surrogate mapping.
        """
        document = Document(str(source))
        blocks = list(iter_text_blocks(document))
        logger.info("Loaded %d text blocks from %s", len(blocks), source)

        self.harvest(blocks)

        result = RedactionResult(stats=RedactionStats(), ner_active=bool(self._ner))
        for block in blocks:
            entities = self.detect(block.text)
            if not entities:
                continue
            replacements = []
            for entity in entities:
                surrogate = self.surrogates.get(entity.pii_type, entity.text)
                replacements.append((entity.start, entity.end, surrogate))
                result.stats.record(entity.pii_type)
                result.entities.append((block.location, entity))
            apply_replacements(block.paragraph, replacements)

        result.blocks_processed = len(blocks)
        if self.config.scrub_metadata:
            result.metadata_fields_scrubbed = scrub_core_properties(document)
        result.mapping = self.surrogates.mapping

        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        document.save(str(destination))
        logger.info("Wrote %s (%d entities redacted)", destination, result.stats.total())
        return result

    # -- reporting ----------------------------------------------------------

    @staticmethod
    def write_audit_log(result: RedactionResult, path: str | Path,
                        include_mapping: bool = False) -> None:
        """Persist a run report.

        The mapping is excluded by default: it is a re-identification key, and
        writing it beside the redacted document would undo the redaction.
        """
        Path(path).write_text(
            json.dumps(result.to_dict(include_mapping=include_mapping), indent=2),
            encoding="utf-8",
        )
