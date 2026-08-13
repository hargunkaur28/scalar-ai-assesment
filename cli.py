#!/usr/bin/env python3
"""Command-line interface for the PII redactor.

Examples:
    python cli.py input.docx -o redacted.docx
    python cli.py input.docx -o out.docx --types PERSON EMAIL PHONE
    python cli.py input.docx -o out.docx --no-ner --audit run.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from redactor import PIIType, RedactionConfig, RedactionPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pii-redact",
        description="Replace personally identifiable information in a .docx with consistent fake values.",
    )
    parser.add_argument("input", type=Path, help="Source .docx file")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Destination .docx file")
    parser.add_argument(
        "--types", nargs="+", choices=[t.value for t in PIIType],
        help="Restrict redaction to these PII types (default: all)",
    )
    parser.add_argument("--no-ner", action="store_true", help="Skip the optional spaCy layer")
    parser.add_argument("--ner-model", default="en_core_web_lg", help="spaCy model to load")
    parser.add_argument("--salt", default="pii-redactor-v1", help="Surrogate mapping salt")
    parser.add_argument("--keep-metadata", action="store_true", help="Do not scrub docx core properties")
    parser.add_argument("--audit", type=Path, help="Write a JSON run report here")
    parser.add_argument(
        "--mapping", type=Path,
        help="Write the real->fake mapping here. SENSITIVE: this file re-identifies the document.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 1

    config = RedactionConfig(
        enabled_types={PIIType(t) for t in args.types} if args.types else None,
        use_ner=not args.no_ner,
        ner_model=args.ner_model,
        salt=args.salt,
        scrub_metadata=not args.keep_metadata,
        emit_mapping=True,
    )

    pipeline = RedactionPipeline(config)
    result = pipeline.redact_docx(args.input, args.output)

    print(f"Redacted {result.stats.total()} entities across {result.blocks_processed} blocks")
    for pii_type, count in sorted(result.stats.counts.items(), key=lambda kv: -kv[1]):
        print(f"  {pii_type:<16} {count:>6}")
    print(f"NER layer active: {result.ner_active}")
    print(f"Output: {args.output}")

    if args.audit:
        RedactionPipeline.write_audit_log(result, args.audit)
        print(f"Audit log: {args.audit}")
    if args.mapping:
        args.mapping.write_text(json.dumps(result.mapping, indent=2), encoding="utf-8")
        print(f"Mapping (sensitive): {args.mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
