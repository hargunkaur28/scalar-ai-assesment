"""PII redaction for Word documents: detect, pseudonymise, and re-emit .docx."""

from .entities import Entity, PIIType, REQUIRED_TYPES
from .pipeline import RedactionConfig, RedactionPipeline, RedactionResult
from .surrogates import SurrogateFactory

__all__ = [
    "Entity",
    "PIIType",
    "REQUIRED_TYPES",
    "RedactionConfig",
    "RedactionPipeline",
    "RedactionResult",
    "SurrogateFactory",
]
__version__ = "1.0.0"
