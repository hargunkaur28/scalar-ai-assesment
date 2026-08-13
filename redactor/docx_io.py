"""Reading text out of a .docx and writing redacted text back in.

The hard part is not finding the text -- it is putting the replacement back
without destroying the document. Word fragments a single visible sentence
across many `<w:r>` runs (one per formatting change, spell-check marker or
revision id), so "KSH International Limited" often does not exist as a
contiguous string anywhere in the XML.

The approach here:

1. Concatenate a paragraph's runs into one string and remember, for each run,
   the character interval it occupies in that string.
2. Detect PII against the concatenated string, so matches can span runs.
3. Write each replacement into the *first* run it touches and delete the
   overlapped slice from the rest, applying replacements right-to-left so
   earlier offsets stay valid.

The alternative -- rewriting `word/document.xml` as raw text -- is faster to
code but loses the guarantee that formatting, tables, and numbering survive.

Coverage note: body paragraphs, tables (recursively, including nested tables),
headers, footers, and the document's core properties are all processed.
Headers, footers and metadata are the three places redaction tools usually
leak, because they are invisible in a normal read-through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Sequence, Tuple

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph


@dataclass
class TextBlock:
    """One addressable unit of text, plus the surrounding text that explains it.

    `context` matters for table cells: a cell containing only "Lokesh Shah" is
    unclassifiable on its own, but its row also contains "Contact Person".
    Recognizers receive the row text as context while only ever redacting
    inside `text`.
    """

    paragraph: Paragraph
    text: str
    context: str = ""
    location: str = "body"
    #: True when this paragraph sits in a table's first row. Header cells hold
    #: column labels ("CONTACT PERSON", "WEIGHTED AVERAGE COST"), which are
    #: shaped exactly like names but never are one.
    is_header: bool = False


def iter_block_items(parent) -> Iterator[object]:
    """Yield paragraphs and tables of `parent` in true document order."""
    from docx.oxml.ns import qn

    if isinstance(parent, DocumentObject):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        parent_elm = parent._element

    for child in parent_elm.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, parent)
        elif child.tag == qn("w:tbl"):
            yield Table(child, parent)


def _table_blocks(table: Table, location: str) -> Iterator[TextBlock]:
    # The header row is part of every cell's context: a column headed
    # "Contact Person" is what identifies the bare name three rows below it.
    try:
        header = " | ".join(cell.text for cell in table.rows[0].cells)
    except (IndexError, AttributeError):
        header = ""

    for index, row in enumerate(table.rows):
        row_text = header + " | " + " | ".join(cell.text for cell in row.cells)
        for cell in row.cells:
            for item in iter_block_items(cell):
                if isinstance(item, Table):
                    yield from _table_blocks(item, location)
                elif item.text.strip():
                    yield TextBlock(item, item.text, row_text, location, index == 0)


def iter_text_blocks(document: Document) -> Iterator[TextBlock]:
    """Yield each paragraph exactly once, with its contexts merged.

    This wrapper exists because of horizontally merged table cells: python-docx
    returns the *same* underlying cell object once per grid column it spans, so
    a merged banner row yields the same paragraph seven times. Writing
    replacements into it seven times, each using offsets computed against the
    original text, shreds the paragraph. Deduplicating on the XML element is
    the fix; contexts from every occurrence are unioned so no harvesting
    signal is lost.
    """
    seen: dict[int, TextBlock] = {}
    order: List[int] = []
    for block in _iter_text_blocks_raw(document):
        key = id(block.paragraph._p)
        if key in seen:
            existing = seen[key]
            if block.context and block.context not in existing.context:
                existing.context += " | " + block.context
            existing.is_header = existing.is_header or block.is_header
            continue
        seen[key] = block
        order.append(key)
    for key in order:
        yield seen[key]


def _iter_text_blocks_raw(document: Document) -> Iterator[TextBlock]:
    """Walk every paragraph in the document, including headers and footers."""
    for item in iter_block_items(document):
        if isinstance(item, Table):
            yield from _table_blocks(item, "body:table")
        elif item.text.strip():
            yield TextBlock(item, item.text, "", "body")

    for index, section in enumerate(document.sections):
        for part_name in ("header", "footer", "first_page_header", "first_page_footer",
                          "even_page_header", "even_page_footer"):
            part = getattr(section, part_name, None)
            if part is None:
                continue
            location = f"section{index}:{part_name}"
            for paragraph in part.paragraphs:
                if paragraph.text.strip():
                    yield TextBlock(paragraph, paragraph.text, "", location)
            for table in part.tables:
                yield from _table_blocks(table, location)


def apply_replacements(paragraph: Paragraph, replacements: Sequence[Tuple[int, int, str]]) -> None:
    """Rewrite `paragraph` in place, preserving run formatting.

    Args:
        paragraph: The paragraph to modify.
        replacements: (start, end, new_text) triples with offsets into the
            paragraph's concatenated run text. Must be non-overlapping.
    """
    runs = paragraph.runs
    if not runs or not replacements:
        return

    spans: List[Tuple[int, int, object]] = []
    cursor = 0
    for run in runs:
        length = len(run.text)
        spans.append((cursor, cursor + length, run))
        cursor += length

    # Right-to-left: edits never invalidate the offsets of edits still pending.
    for start, end, new_text in sorted(replacements, key=lambda r: -r[0]):
        written = False
        for run_start, run_end, run in spans:
            if run_end <= start or run_start >= end:
                continue
            local_start = max(start - run_start, 0)
            local_end = min(end - run_start, run_end - run_start)
            if not written:
                run.text = run.text[:local_start] + new_text + run.text[local_end:]
                written = True
            else:
                run.text = run.text[:local_start] + run.text[local_end:]


def scrub_core_properties(document: Document, placeholder: str = "Redacted") -> List[str]:
    """Blank out document metadata that carries author and company identity.

    Returns the names of the properties that were changed, for the audit log.
    """
    props = document.core_properties
    changed = []
    for field in ("author", "last_modified_by", "company", "manager",
                  "category", "comments", "keywords", "subject", "title"):
        try:
            if getattr(props, field, None):
                setattr(props, field, placeholder)
                changed.append(field)
        except (AttributeError, ValueError):
            continue
    return changed
