# PII Redaction Tool

Detects personally identifiable information in a Word document and replaces it
with consistent fake values, preserving the document's formatting, tables,
headers and footers.

Built against a 56,000-word Indian Red Herring Prospectus. On a held-out
evaluation set it scores **precision 1.000, recall 0.971 (relaxed matching)**;
see [EVALUATION.md](EVALUATION.md) for the full report, including the
failure cases.

```bash
pip install -r requirements.txt
python cli.py input.docx -o redacted.docx
```

---

## Approach: hybrid, not one technique

Three detection strategies, each used where it is strongest.

**1. Regex + checksums — for structured PII.**
Emails, phone numbers, IPs, SSNs, credit cards, PANs, Aadhaar numbers and CINs
have rigid grammars, so patterns beat a statistical model on both precision
and speed. Where a raw pattern would be unsafe, it is paired with a validator:
credit cards must pass **Luhn**, Aadhaar must pass **Verhoeff**, SSNs must
avoid reserved ranges, IPv4 octets must be in range. The checksum is what
stops a 16-digit invoice number being redacted as a card — roughly 90% of
random digit strings fail Luhn.

Three types are additionally **context-gated**, meaning the pattern only fires
when a keyword appears nearby: `DATE_OF_BIRTH`, `DIN` and `BANK_ACCOUNT`. A
prospectus is saturated with dates — board resolutions, certificates, bid
windows — so an ungated date recognizer would have near-zero precision. It
only fires within 40 characters of "date of birth", "born on" or "DOB".

**2. Two-pass harvesting — for names, companies and addresses.**
These have no fixed grammar. Rather than classifying each mention locally, the
tool does:

- **Pass A (harvest)** — walk the whole document, collect candidates that
  appear in a high-confidence context *at least once*: next to an honorific
  (`Mr. Rohan Dey`), inside a personal email local part
  (`tushar.gavankar@` → `Tushar Gavankar`), in a table row that also contains
  a job title, in a `Role: A, B, C` banner, or immediately before their own
  title (`Sandesh Bhagwat, CEO`).
- **Pass B (propagate)** — redact every occurrence of every harvested
  candidate, everywhere.

The reason for the split: a name only has to be identifiable *once* to be
redacted *everywhere*. "Rakhi Girija Shetty" sits beside "Whole-time Director"
in one table on page 60; that single sighting is what licenses redacting the
bare name on page 250, where there is no local context at all. This is what
lifts PERSON recall to 1.000 on the real document without loosening the
precision guard.

Two derived propagation rules extend this:
- **Short forms.** From `Kushal Subbayya Hegde`, also redact `Kushal Hegde`.
- **Surname families.** Once `Hegde` is confirmed as a surname, a capitalised
  token immediately before it is a given name — which recovers
  `Karunakar Hegde` and `Narayna B. Shetty` from litigation tables where no
  role appears.

Bare surnames are deliberately *not* propagated alone; many Indian surnames
double as place names or common nouns, and the recall is not worth the
precision.

**3. Optional spaCy NER — as a supplement.**
Loaded if installed, skipped with a warning if not. It scores at 0.6
confidence so it loses overlap ties to the deterministic recognizers. It
catches names that never appear in any high-confidence context — the one
category the harvester structurally cannot reach.

**Why not Presidio?** It is the obvious choice and it is a good library. It is
not used here because its person/org detection is spaCy underneath (so the
same layer, with more dependency weight), its recognizers are tuned for US and
EU formats rather than Indian ones, and — decisively — the two-pass
document-wide harvesting above is not something Presidio does. Presidio
classifies each text independently. The `RegexRecognizer` base class is
deliberately shaped like a Presidio recognizer so the regex layer could be
ported if you wanted its ecosystem.

---

## Scope decisions (the judgment calls)

The brief asks for these to be explicit. Each is encoded as data in
`redactor/gazetteer.py`, not buried in logic.

| Decision | Choice | Reasoning |
|---|---|---|
| Regulators, exchanges, depositories (SEBI, BSE, NSE, RBI, CDSL) | **Not redacted** | Naming SEBI tells you nothing about who is involved; every Indian offer document names it. Redacting would destroy meaning for zero privacy gain. |
| Statutes and regulations (Companies Act, SEBI ICDR Regulations) | **Not redacted** | Named laws are not personal data. |
| Commercial counterparties (issuer, bankers, auditors, law firms, vendors) | **Redacted** | These identify the transaction and its participants. |
| Corporate websites | **Redacted** | `www.kshinternational.com` identifies the issuer as directly as its name. Redacting the name and leaving the domain would be theatre. |
| Page numbers, share counts, percentages, rupee figures, fiscal years | **Not redacted** | Not identifying, and redacting them makes the document useless. |
| DIN (Director Identification Number) | **Redacted, context-gated** | Arguably public register data, but it maps 1:1 to a named individual, so it is a re-identification key for an otherwise-redacted director. Disable with `--types` if you disagree. |
| CIN (Corporate Identity Number) | **Redacted** | Same reasoning — a unique key back to the redacted company. |
| Role labels ("Independent Director", "Contact Person") | **Not redacted** | They are labels sitting next to PII, not PII. Getting this wrong is the single biggest precision trap in this document. |

---

## Replacement strategy

Surrogates are **consistent and deterministic**: the same real value maps to
the same fake value everywhere, derived from a salted SHA-256 of the
normalised source. This matters more than it sounds. If "Kushal Subbayya
Hegde" became a different person on each of his 40 mentions, the redacted
prospectus would be incoherent and useless for the downstream testing it
exists to enable. Determinism also makes the evaluation reproducible — two
runs produce byte-identical output.

Surrogates preserve the *shape* of what they replace:

- Phone numbers keep their country code, digit count and separators.
- Credit card surrogates are **Luhn-valid**, so downstream validators pass.
- Emails are derived from the person surrogate, so `rohan.dey@gmail.com`
  becomes `maya.valdez@example.com` and matches the name it belongs to.
- IPs land in `198.51.100.0/24` (TEST-NET-2, RFC 5737).
- ALL-CAPS originals get ALL-CAPS surrogates, so cover pages keep their look.

Rotate `--salt` to get a different but still-consistent mapping.

---

## Document handling

Word fragments a single visible sentence across many `<w:r>` runs, so
"KSH International Limited" often does not exist as a contiguous string
anywhere in the XML. The tool concatenates a paragraph's runs, detects against
the concatenation, then writes each replacement into the first run it touches
and deletes the overlapped slice from the rest — applying replacements
right-to-left so pending offsets stay valid. Formatting, tables and numbering
survive.

Coverage: body paragraphs, tables (recursively, including nested), **headers,
footers, and document core properties** (author, company, last-modified-by).
Those last three are where redaction tools usually leak, because they are
invisible in a normal read-through.

> **Bug worth knowing about if you extend this:** python-docx returns the same
> cell object once per grid column a merged cell spans. A merged banner row
> yielded the same paragraph seven times, and applying replacements seven
> times — each using offsets from the original text — shredded it into
> gibberish. `iter_text_blocks` deduplicates on the XML element and unions the
> contexts.

---

## False positives and negatives observed

Concrete, from actual runs. Full analysis in [EVALUATION.md](EVALUATION.md).

**False negatives**
- **Addresses with no PIN code.** The recognizer anchors on a six-digit PIN;
  `Gat No. 11/3, 11/4, 11/5, Village Birdewadi` is invisible to it.
- **Names appearing exactly once with no role nearby.** `Karunakar Bhandary`
  in the share transfer history. The NER layer is the fix.
- **Address spans stop at paragraph boundaries**, so a wrapped cover-page
  address leaves `Maharashtra, India` on the next line. Surrogates are shaped
  to end at the PIN so this reads naturally; the residue is not identifying.

**False positives**
- **`10.0.0.1` in "Version 10.0.0.1 of the firmware"** — a version string that
  is also a valid private IPv4. Kept deliberately: an over-redacted version
  string is cheap, a missed internal IP is not.
- **Type confusion.** `Waterloo Motors` is a company without a legal suffix
  and can be labelled PERSON. The text is still redacted, just under the wrong
  label.

**Traps that were closed during development**, each of which cost real
precision before being fixed: role labels harvested as names ("Managing
Director" became a person); org spans walking across commas to swallow three
companies as one; `Equity Share capital` and `Refund Bank` read as company
names because *Capital* and *Bank* are legitimate suffixes; abbreviation
periods (`S. no. 245`) treated as sentence boundaries, truncating addresses.

---

## Extending to a new PII type

For a **structured** type, add one class and one list entry:

```python
# redactor/recognizers/regex_recognizers.py
class PassportRecognizer(RegexRecognizer):
    pii_type = PIIType.PASSPORT
    score = 0.95
    context_keywords = ["passport"]      # optional gate
    pattern = re.compile(r"\b[A-PR-WY][1-9]\d{5}[1-9]\b")

    def validate(self, match):           # optional checksum
        return True

REGEX_RECOGNIZERS = [..., PassportRecognizer()]
```

Then add `PASSPORT` to `PIIType`, a generator to `SurrogateFactory._generate`,
and gold examples to `evaluation/make_synthetic.py`. Nothing else changes —
the pipeline, overlap resolution and docx writer are type-agnostic.

For an **unstructured** type, implement any object with an `analyse(text)`
method yielding `Entity` objects and append it in
`RedactionPipeline._build_recognizers`.

To change *policy* rather than capability, edit the word lists in
`redactor/gazetteer.py`. They are plain data so a reviewer can audit the scope
decisions without reading any logic.

---

## Usage

```bash
# Basic
python cli.py input.docx -o redacted.docx

# Only certain types
python cli.py input.docx -o out.docx --types PERSON EMAIL PHONE

# Deterministic-only (skip spaCy), with a run report
python cli.py input.docx -o out.docx --no-ner --audit run.json

# Different surrogate mapping
python cli.py input.docx -o out.docx --salt my-secret-salt
```

`--mapping` writes the real→fake table. **Treat it as sensitive** — it
re-identifies the document, and is excluded from the audit log by default for
that reason.

Web UI:

```bash
pip install -r requirements-app.txt   # core + Streamlit
streamlit run app.py
```

Dependencies are split so a deployment installs only what it runs:
`requirements.txt` is the core (python-docx alone), `requirements-app.txt`
adds Streamlit, `requirements-ner.txt` adds the optional spaCy and Faker
layers. See [DEPLOY.md](DEPLOY.md) for the two hosted deployments.

---

## Layout

```
redactor/
  entities.py                        PII taxonomy, Entity, overlap resolution
  gazetteer.py                       Scope policy as plain word lists
  surrogates.py                      Deterministic consistent fake generation
  docx_io.py                         Run-level read/write, headers, metadata
  pipeline.py                        Harvest -> detect -> resolve -> replace
  recognizers/
    regex_recognizers.py             12 pattern recognizers + checksums
    context_recognizers.py           Two-pass name/org/address harvesting
    ner.py                           Optional spaCy layer
cli.py                               Command line
app.py                               Streamlit web UI (the Render deployment)
api/redact.py                        HTTP function (the Vercel deployment)
public/index.html                    Upload UI served by Vercel
evaluation/
  ground_truth.json                  Development set (60 blocks)
  ground_truth_heldout.json          Held-out set (22 blocks)
  make_synthetic.py                  Builds the synthetic test document
  evaluate.py                        Precision / recall / F1 / token accuracy
tests/test_redactor.py               Unit tests
```

---

## Performance

~30 seconds for the 4,027-block, 56,000-word prospectus in the
deterministic-only configuration, single-threaded. The spaCy layer roughly
triples this. Detection is per-block and embarrassingly parallel if needed.

## A note on the brief

The assignment text refers to reading "the ticket log"; the attached document
is a Red Herring Prospectus. I have treated the prospectus as the input, which
is what the deliverables ask for.
