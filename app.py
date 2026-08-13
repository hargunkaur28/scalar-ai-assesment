"""Streamlit front end for the PII redactor.

Deployed to satisfy the assignment's cloud-hosting requirement. Kept
intentionally thin: all logic lives in `redactor/`, so the web app and the CLI
cannot drift apart.

Run locally:
    streamlit run app.py
"""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import streamlit as st

from redactor import PIIType, RedactionConfig, RedactionPipeline

st.set_page_config(page_title="PII Redaction Tool", page_icon="🔒", layout="wide")

st.title("PII Redaction Tool")
st.caption(
    "Upload a .docx. Personally identifiable information is replaced with "
    "consistent fake values — the same real name becomes the same fake name "
    "everywhere in the document."
)

with st.sidebar:
    st.header("Settings")

    all_types = [t.value for t in PIIType]
    selected = st.multiselect(
        "PII types to redact",
        options=all_types,
        default=all_types,
        help="Leave all selected to redact everything the tool can detect.",
    )

    use_ner = st.checkbox(
        "Use spaCy NER layer",
        value=False,
        help=(
            "Catches names that never appear next to a job title or honorific. "
            "Slower, and requires the model to be installed."
        ),
    )

    scrub_metadata = st.checkbox(
        "Scrub document metadata",
        value=True,
        help="Blanks author, company and last-modified-by in the file's core properties.",
    )

    salt = st.text_input(
        "Surrogate salt",
        value="pii-redactor-v1",
        help="Changing this produces a different — but still internally consistent — mapping.",
    )

    st.divider()
    st.caption(
        "Scope: regulators, exchanges and statutes (SEBI, BSE, RBI, Companies "
        "Act) are deliberately **not** redacted. See the README for the full "
        "policy."
    )

uploaded = st.file_uploader("Word document (.docx)", type=["docx"])

if uploaded is None:
    st.info("Upload a .docx file to begin.")
    st.stop()

if not selected:
    st.warning("Select at least one PII type.")
    st.stop()

if st.button("Redact document", type="primary"):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / uploaded.name
        source.write_bytes(uploaded.getbuffer())
        destination = Path(tmp) / f"{Path(uploaded.name).stem}_REDACTED.docx"

        config = RedactionConfig(
            enabled_types={PIIType(t) for t in selected},
            use_ner=use_ner,
            salt=salt,
            scrub_metadata=scrub_metadata,
        )

        with st.spinner("Harvesting entities and redacting…"):
            pipeline = RedactionPipeline(config)
            try:
                result = pipeline.redact_docx(source, destination)
            except Exception as exc:  # surface the error rather than a blank page
                st.error(f"Redaction failed: {exc}")
                st.stop()

        data = destination.read_bytes()

    st.success(
        f"Redacted {result.stats.total()} entities across "
        f"{result.blocks_processed} text blocks."
    )

    left, right = st.columns([2, 1])

    with left:
        st.subheader("Entities redacted by type")
        counts = sorted(result.stats.counts.items(), key=lambda kv: -kv[1])
        if counts:
            st.bar_chart({label: value for label, value in counts})
            st.table([{"Type": label, "Count": value} for label, value in counts])
        else:
            st.write("No PII detected.")

    with right:
        st.subheader("Download")
        st.download_button(
            "Redacted .docx",
            data=data,
            file_name=f"{Path(uploaded.name).stem}_REDACTED.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
        )
        st.download_button(
            "Audit log (.json)",
            data=json.dumps(result.to_dict(), indent=2),
            file_name="audit.json",
            mime="application/json",
        )
        st.caption(
            f"NER layer active: **{result.ner_active}**  \n"
            f"Metadata fields scrubbed: "
            f"{', '.join(result.metadata_fields_scrubbed) or 'none'}"
        )

    with st.expander("Surrogate mapping (sensitive — this re-identifies the document)"):
        st.warning(
            "This table maps real values back to their replacements. Storing it "
            "alongside the redacted file undoes the redaction."
        )
        rows = [
            {"Type": pii_type, "Original": original, "Replacement": replacement}
            for pii_type, entries in sorted(result.mapping.items())
            for original, replacement in sorted(entries.items())
        ]
        st.dataframe(rows, use_container_width=True)
