#!/usr/bin/env python3
"""Score the redactor against hand-annotated ground truth.

Metrics reported, and why each is here:

  Strict precision/recall/F1   A prediction counts only if its type AND its
                               exact character span match a gold entity. This
                               is the honest headline number.

  Relaxed precision/recall/F1  A prediction counts if its type matches and its
                               span overlaps the gold span. This separates
                               "found the entity but drew the boundary a word
                               short" from "missed it entirely" -- a
                               distinction that matters for redaction, where a
                               partially-correct span still removes most of the
                               sensitive text.

  Token accuracy               Fraction of whitespace tokens whose label
                               (including the O / not-PII label) matches gold.
                               Included because the brief asks for accuracy;
                               note that it is dominated by the O class and so
                               always looks high. It is the least informative
                               number here and should not be read as the
                               headline.

Matching is greedy and one-to-one: each gold entity can be claimed by at most
one prediction, so duplicate predictions over the same span score as false
positives rather than free true positives.

Usage:
    python evaluation/evaluate.py --docx data/Red_Herring_Prospectus.docx \\
        --truth evaluation/ground_truth.json --report evaluation/report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from docx import Document  # noqa: E402

from redactor import PIIType, RedactionConfig, RedactionPipeline  # noqa: E402
from redactor.docx_io import iter_text_blocks  # noqa: E402
from redactor.entities import Entity  # noqa: E402


@dataclass
class Span:
    """A gold or predicted entity reduced to what scoring needs."""

    start: int
    end: int
    label: str
    text: str

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def support(self) -> int:
        return self.tp + self.fn

    def add(self, other: "Counts") -> None:
        self.tp += other.tp
        self.fp += other.fp
        self.fn += other.fn


def locate(text: str, needle: str) -> Optional[Tuple[int, int]]:
    """Find `needle` in `text`, tolerating whitespace and dash differences.

    Ground truth is annotated by copying visible text, which does not always
    reproduce the document's non-breaking spaces, tabs or en-dashes byte for
    byte. Rather than silently dropping such labels -- which would quietly
    inflate recall -- this falls back to a whitespace-and-dash-insensitive
    regex, and the caller raises if even that fails.
    """
    index = text.find(needle)
    if index != -1:
        return index, index + len(needle)

    pattern = r"\s*".join(
        re.escape(tok).replace(r"\-", r"[-\u2010-\u2015]")
        for tok in needle.split()
    )
    match = re.search(pattern, text)
    return (match.start(), match.end()) if match else None


def load_ground_truth(path: Path, docx_path: Optional[Path]) -> List[dict]:
    """Resolve annotated (type, surface) pairs into character spans."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = []

    texts: Dict[int, str] = {}
    if docx_path is not None:
        blocks = [b for b in iter_text_blocks(Document(str(docx_path))) if b.text.strip()]
        texts = {i: b.text for i, b in enumerate(blocks)}

    for block in payload["blocks"]:
        text = block.get("text") or texts.get(block["index"])
        if text is None:
            raise KeyError(
                f"Block {block.get('index')} not found in {docx_path}. "
                "Ground truth indices are tied to a specific input document."
            )
        spans = []
        for label, surface in block["entities"]:
            found = locate(text, surface)
            if found is None:
                raise ValueError(
                    f"Ground-truth label {label}='{surface}' does not occur in "
                    f"block {block.get('index')}: {text[:120]!r}"
                )
            spans.append(Span(found[0], found[1], label, surface))
        records.append(
            {"text": text, "gold": spans, "stratum": block.get("stratum", "all")}
        )
    return records


def to_spans(entities: Iterable[Entity]) -> List[Span]:
    return [Span(e.start, e.end, e.pii_type.value, e.text) for e in entities]


def match(gold: Sequence[Span], pred: Sequence[Span], strict: bool) -> Counts:
    """Greedy one-to-one matching between gold and predicted spans."""
    counts = Counts()
    claimed = set()
    for p in pred:
        hit = None
        for index, g in enumerate(gold):
            if index in claimed or g.label != p.label:
                continue
            same = (g.start == p.start and g.end == p.end) if strict else g.overlaps(p)
            if same:
                hit = index
                break
        if hit is None:
            counts.fp += 1
        else:
            claimed.add(hit)
            counts.tp += 1
    counts.fn = len(gold) - len(claimed)
    return counts


def token_accuracy(text: str, gold: Sequence[Span], pred: Sequence[Span]) -> Tuple[int, int]:
    """Return (correct_tokens, total_tokens) using per-token PII labels."""
    def label_at(spans: Sequence[Span], start: int, end: int) -> str:
        for s in spans:
            if s.start < end and start < s.end:
                return s.label
        return "O"

    correct = total = 0
    for token in re.finditer(r"\S+", text):
        total += 1
        if label_at(gold, *token.span()) == label_at(pred, *token.span()):
            correct += 1
    return correct, total


def evaluate(records: List[dict], pipeline: RedactionPipeline) -> dict:
    """Run the pipeline over every annotated block and aggregate metrics."""
    strict: Dict[str, Counts] = {}
    relaxed: Dict[str, Counts] = {}
    by_stratum: Dict[str, Counts] = {}
    errors = {"false_positives": [], "false_negatives": []}
    correct_tokens = total_tokens = 0

    for record in records:
        text = record["text"]
        gold = record["gold"]
        pred = to_spans(pipeline.detect(text))

        labels = {s.label for s in gold} | {s.label for s in pred}
        for label in labels:
            g = [s for s in gold if s.label == label]
            p = [s for s in pred if s.label == label]
            strict.setdefault(label, Counts()).add(match(g, p, strict=True))
            relaxed.setdefault(label, Counts()).add(match(g, p, strict=False))

        stratum_counts = match(gold, pred, strict=False)
        by_stratum.setdefault(record["stratum"], Counts()).add(stratum_counts)

        # Record concrete errors so the report can show them, not just numbers.
        matched = {(s.label, s.start) for s in gold if any(
            s.label == q.label and s.overlaps(q) for q in pred)}
        for s in pred:
            if not any(s.label == g.label and s.overlaps(g) for g in gold):
                errors["false_positives"].append({"label": s.label, "text": s.text})
        for s in gold:
            if (s.label, s.start) not in matched:
                errors["false_negatives"].append({"label": s.label, "text": s.text})

        c, t = token_accuracy(text, gold, pred)
        correct_tokens += c
        total_tokens += t

    def table(counts: Dict[str, Counts]) -> dict:
        return {
            label: {
                "support": c.support, "tp": c.tp, "fp": c.fp, "fn": c.fn,
                "precision": round(c.precision, 4),
                "recall": round(c.recall, 4),
                "f1": round(c.f1, 4),
            }
            for label, c in sorted(counts.items())
        }

    def micro(counts: Dict[str, Counts]) -> dict:
        total = Counts()
        for c in counts.values():
            total.add(c)
        return {
            "precision": round(total.precision, 4),
            "recall": round(total.recall, 4),
            "f1": round(total.f1, 4),
            "tp": total.tp, "fp": total.fp, "fn": total.fn,
        }

    def macro(counts: Dict[str, Counts]) -> dict:
        scored = [c for c in counts.values() if c.support]
        if not scored:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
        return {
            "precision": round(sum(c.precision for c in scored) / len(scored), 4),
            "recall": round(sum(c.recall for c in scored) / len(scored), 4),
            "f1": round(sum(c.f1 for c in scored) / len(scored), 4),
        }

    return {
        "blocks_evaluated": len(records),
        "strict": {"per_type": table(strict), "micro": micro(strict), "macro": macro(strict)},
        "relaxed": {"per_type": table(relaxed), "micro": micro(relaxed), "macro": macro(relaxed)},
        "by_stratum_relaxed": {
            name: {
                "precision": round(c.precision, 4),
                "recall": round(c.recall, 4),
                "f1": round(c.f1, 4),
                "support": c.support,
            }
            for name, c in sorted(by_stratum.items())
        },
        "token_accuracy": round(correct_tokens / total_tokens, 4) if total_tokens else 0.0,
        "errors": errors,
    }


def print_report(title: str, report: dict) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    print(f"Blocks evaluated: {report['blocks_evaluated']}")
    print(f"Token accuracy:   {report['token_accuracy']:.4f}")
    for mode in ("strict", "relaxed"):
        print(f"\n-- {mode.upper()} matching --")
        print(f"{'TYPE':<16}{'SUP':>5}{'TP':>5}{'FP':>5}{'FN':>5}"
              f"{'PREC':>9}{'REC':>9}{'F1':>9}")
        for label, row in report[mode]["per_type"].items():
            print(f"{label:<16}{row['support']:>5}{row['tp']:>5}{row['fp']:>5}"
                  f"{row['fn']:>5}{row['precision']:>9.3f}{row['recall']:>9.3f}"
                  f"{row['f1']:>9.3f}")
        m = report[mode]["micro"]
        mac = report[mode]["macro"]
        print(f"{'MICRO AVG':<16}{'':>20}{m['precision']:>9.3f}"
              f"{m['recall']:>9.3f}{m['f1']:>9.3f}")
        print(f"{'MACRO AVG':<16}{'':>20}{mac['precision']:>9.3f}"
              f"{mac['recall']:>9.3f}{mac['f1']:>9.3f}")
    if report["by_stratum_relaxed"]:
        print("\n-- By stratum (relaxed) --")
        for name, row in report["by_stratum_relaxed"].items():
            print(f"  {name:<10} P={row['precision']:.3f}  R={row['recall']:.3f}  "
                  f"F1={row['f1']:.3f}  (gold n={row['support']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, help="Source document for index-based truth")
    parser.add_argument("--truth", type=Path, required=True, help="Ground-truth JSON")
    parser.add_argument("--report", type=Path, help="Write the full report as JSON")
    parser.add_argument("--no-ner", action="store_true", help="Disable the spaCy layer")
    parser.add_argument("--title", default="Evaluation")
    args = parser.parse_args()

    records = load_ground_truth(args.truth, args.docx)
    pipeline = RedactionPipeline(RedactionConfig(use_ner=not args.no_ner))

    # Harvest over the whole document when one is supplied, so that pass A sees
    # the same evidence it would in a real run. Harvesting only over the sampled
    # blocks would understate recall, since a name confirmed on page 60 is what
    # licenses redacting it on page 250.
    if args.docx:
        blocks = [b for b in iter_text_blocks(Document(str(args.docx))) if b.text.strip()]
        pipeline.harvest(blocks)
    else:
        for record in records:
            pipeline.context.harvest(record["text"], record["text"])
        pipeline.context.finalise()
        pipeline._build_recognizers()

    report = evaluate(records, pipeline)
    report["ner_active"] = bool(pipeline._ner)
    print_report(args.title, report)

    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nFull report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
