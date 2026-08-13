# Evaluation Report

## 1. What is being measured, and why this design

Redaction is an asymmetric problem. A missed entity is a privacy breach; a
false positive is a readability cost. So this evaluation reports precision and
recall separately per PII type rather than collapsing them into one score, and
it reports two matching modes so that "found it but drew the boundary a word
short" is distinguishable from "missed it".

### Three evaluation sets

| Set | Size | Purpose |
|---|---|---|
| **Development** (`ground_truth.json`) | 60 blocks | Used while tuning. Reported for transparency only. |
| **Held-out** (`ground_truth_heldout.json`) | 22 blocks | Drawn and annotated *after* tuning was frozen. **These are the headline numbers.** |
| **Synthetic** (`data/synthetic_pii.docx`) | 18 blocks | The four PII types absent from the real document. |

The three-set split exists because of a problem that would otherwise be
invisible: the development set scores a perfect 1.000 on every type. That is
not a result, it is a symptom. Gazetteers and stopword lists were iterated
against those 60 blocks until the errors went away, so the score measures
memorisation, not generalisation. The held-out set was sampled with a
different seed, annotated without looking at any predictions, and scored once.

### Why a synthetic set at all

The brief names nine PII types. The supplied prospectus contains six of them.
It has **no SSNs, no credit card numbers, no IP addresses and no dates of
birth** — I checked by pattern search across all 56,000 words before writing a
line of detection code. Evaluating only against the real document would leave
four of the nine requirements with an empty confusion matrix and no evidence
the detectors work.

The synthetic document therefore contains known-gold instances of those four
types *plus* deliberate near-misses designed to fail: a structurally invalid
SSN (`000-45-6789`), a Luhn-failing card number, an out-of-range IPv4
(`999.999.999.999`), a version string shaped like an IP (`10.0.0.1`), and
dates with no birth context. Precision on that set is measured against the
confusions that actually happen, not against blank text.

### Sampling

The real-document sets use two strata:

- **Dense** — purposively selected PII-bearing blocks (cover page, banker and
  registrar tables, director and KMP listings). Measures **recall**, because
  a uniform sample of a prospectus is mostly numeric table cells.
- **Random** — uniform random blocks. Measures **precision** on ordinary
  content: percentages, share counts, page references, defined terms.

A uniform-only sample would have produced ~1 gold entity per 25 blocks and a
recall estimate with no useful precision. The trade-off is that the pooled
micro-average is not representative of the document as a whole; per-stratum
numbers are reported alongside it.

### Matching rules

- **Strict** — type *and* exact character span must match.
- **Relaxed** — type matches and spans overlap.
- Matching is greedy and one-to-one, so two predictions over one gold entity
  score as one true positive and one false positive, not two true positives.
- **Token accuracy** is the fraction of whitespace tokens whose label
  (including the `O` non-PII label) matches gold. It is reported because the
  brief asks for accuracy, but it is dominated by the `O` class and will look
  high for any non-catastrophic system. **It is the least informative number
  in this report** and should not be read as the headline.

### Labelling policy

Decisions taken when annotating, applied consistently to both sets:

- **Not PII:** regulators, exchanges, depositories and multilateral bodies
  (SEBI, BSE, NSE, RBI, IMF, CDSL); statutes and regulations; role labels
  ("Promoter Selling Shareholder", "Independent Director"); page numbers,
  share counts, percentages, rupee figures, fiscal years.
- **PII:** individuals' names, corporate counterparties, emails, phone
  numbers, postal addresses, corporate websites, CINs.
- **Websites are treated as PII.** `www.kshinternational.com` identifies the
  issuer as directly as its name; redacting the name but leaving the domain
  would be theatre.

---

## 2. Headline results — held-out set

22 blocks, 35 gold entities, annotated post-freeze, scored once.

### Relaxed matching

| Type | Support | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ADDRESS | 2 | 1 | 0 | 1 | 1.000 | 0.500 | 0.667 |
| EMAIL | 10 | 10 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANISATION | 2 | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 11 | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 6 | 6 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| WEBSITE | 4 | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro avg** | **35** | **34** | **0** | **1** | **1.000** | **0.971** | **0.986** |
| **Macro avg** | | | | | **1.000** | **0.917** | **0.944** |

### Strict matching

| Type | Support | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ADDRESS | 2 | 0 | 1 | 2 | 0.000 | 0.000 | 0.000 |
| EMAIL | 10 | 10 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANISATION | 2 | 2 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 11 | 11 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 6 | 5 | 1 | 1 | 0.833 | 0.833 | 0.833 |
| WEBSITE | 4 | 4 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| **Micro avg** | **35** | **32** | **2** | **3** | **0.941** | **0.914** | **0.927** |
| **Macro avg** | | | | | **0.806** | **0.806** | **0.806** |

**Token accuracy: 0.9848**

The gap between strict (0.927 F1) and relaxed (0.986 F1) is entirely
boundary drift on two entities — one address and one phone number where the
detected span starts or ends a token away from the annotation. Both still
remove the sensitive content.

### The one genuine miss

`Gat No. 11/3, 11/4, 11/5, Village Birdewadi` — an address written without a
PIN code. The address recognizer anchors on a six-digit PIN and expands
outward, so an address with no PIN is invisible to it. This is a known
architectural limitation, not a tuning gap; see §5.

---

## 3. Synthetic set — the four otherwise-untested types

18 blocks, 23 gold entities.

| Type | Support | TP | FP | FN | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **SSN** | 2 | 2 | 0 | 0 | **1.000** | **1.000** | **1.000** |
| **CREDIT_CARD** | 4 | 4 | 0 | 0 | **1.000** | **1.000** | **1.000** |
| **DATE_OF_BIRTH** | 3 | 3 | 0 | 0 | **1.000** | **1.000** | **1.000** |
| **IP_ADDRESS** | 3 | 3 | 1 | 0 | **0.750** | **1.000** | **0.857** |
| AADHAAR | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PAN | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ADDRESS | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| EMAIL | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| ORGANISATION | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PHONE | 1 | 1 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| PERSON | 5 | 1 | 1 | 4 | 0.500 | 0.200 | 0.286 |

**Token accuracy: 0.9615**

All decoys behaved as intended: the invalid SSNs, the Luhn-failing card, the
out-of-range IPv4, the non-birth dates and the allowlisted public bodies were
all correctly left alone.

### The two informative failures

**IP false positive — `10.0.0.1` in "Version 10.0.0.1 of the firmware".**
A version string that is also a syntactically valid private IPv4. Distinguishing
them requires reading the sentence, not the token. I chose to keep the false
positive rather than add a "not preceded by *version*" hack, because in a
redaction tool an over-redacted version string is cheap and a missed internal
IP is not.

**PERSON recall collapses to 0.200 on this set — and that is the single most
important number in this report.** It is not a bug; it is the architecture
being measured honestly. The person detector works by harvesting names from
high-confidence contexts anywhere in the document and then propagating them
everywhere. On an 18-paragraph synthetic file, "Priya Raghunathan" and
"Marcus Whitfield" appear once each with no honorific and no job title
adjacent, so there is nothing to harvest from and nothing to propagate. On the
real 4,027-block prospectus the same detector scores **1.000 recall on 11 gold
names**, because every individual is introduced somewhere with a title beside
them.

The practical reading: **this system's person detection degrades sharply on
short documents and on names that never co-occur with a role.** That is
precisely the gap the optional spaCy layer fills, and it is why the layer
exists even though the deterministic path carries the load on this document.

---

## 4. Development set (reported for transparency, not as a result)

60 blocks, 41 gold entities. **Strict and relaxed both: precision 1.000,
recall 1.000, F1 1.000. Token accuracy 1.000.**

This is what tuning against your own test set produces. It is included so the
overfitting is visible rather than hidden, and it is the reason the held-out
set exists. Any submission reporting only a number like this should be
treated with suspicion.

---

## 5. Known limitations

Ordered by how much they would worry me in production.

1. **Addresses without a PIN code are missed.** The recognizer anchors on a
   six-digit PIN. `Gat No. 11/3, 11/4, 11/5, Village Birdewadi` has none.
   Fixing this properly needs either a locality gazetteer or a sequence model;
   a looser regex would flood a document this numeric with false positives.

2. **Address spans do not cross paragraph boundaries.** Where a cover-page
   address is split across two lines, the street and PIN are replaced and the
   trailing `Maharashtra, India` survives on the next paragraph. Mitigated by
   generating surrogates that end at the PIN, so the residue reads as a natural
   continuation. The residual text is not identifying on its own.

3. **Person recall depends on document-wide context.** See §3. A name
   appearing exactly once, in prose, with no honorific and no adjacent role,
   is not detected without the NER layer. `Karunakar Bhandary` in the share
   transfer history is a real example from this document.

4. **The spaCy layer could not be exercised here.** The environment this was
   built in has no network access, so `en_core_web_lg` could not be installed
   and every number above is from the **deterministic-only** configuration.
   The layer is wired in, degrades gracefully when absent, and scores at 0.6
   confidence so it loses overlap ties to the deterministic recognizers — but
   it is untested, and I would not claim numbers for it. Reproduce with the
   `--no-ner` flag removed.

5. **Type confusion between PERSON and ORGANISATION.** Entities like
   `Waterloo Motors` are companies without a legal suffix; a token gazetteer
   catches the common cases, but the boundary is fuzzy. The text still gets
   redacted, just under the wrong label — a metrics problem more than a
   privacy one.

6. **Ground truth is single-annotator.** No inter-annotator agreement was
   computed. On judgment calls — is "Chartered Accountants" part of the firm
   name? — my label is the only vote.

7. **Sample size.** 35 held-out gold entities is small. A single error moves
   micro-recall by ~3 points, and per-type figures for ADDRESS (n=2) and
   ORGANISATION (n=2) should be read as directional only.

---

## 5a. Residual leak audit of the full production run

Metrics on a sample are not the same as checking the actual deliverable. After
producing the final redacted document (576 entities across 4,027 blocks), I
grepped the output for every distinctive real PII string I could think of.

Fully eliminated (0 occurrences): `Hegde`, `Shetty`, `Malvadkar`,
`Gavankar`, `Erandawane`, `kshinternational`, `hdfcbank`, `icicisecurities`,
and every other email domain.

Three residuals remain, all traceable to a documented limitation:

| Residual | Count | Cause |
|---|---:|---|
| `Birdewadi` | 4 | A village name used as a facility label ("the Birdewadi facility") rather than inside a PIN-anchored address. Limitation 1. |
| `Nuvama` | 6 | The banker referred to by bare single-token short name. The alias mechanism registers `Nuvama Wealth Management` from the full legal name but requires two tokens, because propagating single-token aliases would fire on ordinary words. |
| `Bhandary` | 2 | A surname never independently confirmed anywhere in the document. Limitation 3. |

All three would be caught by the NER layer. I deliberately did **not** tune
them away after the held-out evaluation was scored: fixing them by extending
gazetteers post-hoc is exactly the overfitting the held-out set exists to
detect, and the resulting numbers would no longer mean anything.

The honest characterisation: the document is thoroughly redacted for
individuals and their contact details, and has a small residue of
weakly-identifying organisational and locality references.



```bash
pip install -r requirements.txt
python evaluation/make_synthetic.py --out data/synthetic_pii.docx

# Headline
python evaluation/evaluate.py --docx data/Red_Herring_Prospectus.docx \
    --truth evaluation/ground_truth_heldout.json --no-ner

# Four otherwise-untested types
python evaluation/evaluate.py --truth data/synthetic_pii.truth.json --no-ner

# Development set (overfit; for transparency)
python evaluation/evaluate.py --docx data/Red_Herring_Prospectus.docx \
    --truth evaluation/ground_truth.json --no-ner
```

Runs are deterministic: surrogates are derived from a salted SHA-256 of the
source value, so repeated runs produce byte-identical output.
