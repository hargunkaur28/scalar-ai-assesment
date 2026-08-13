"""Vercel serverless entry point for the redactor.

Vercel cannot host `app.py`: Streamlit needs a long-lived process holding a
WebSocket per session, and a Vercel Function is a request-scoped lambda. So the
hosted-on-Vercel surface is this file plus `public/index.html` -- a thin HTTP
skin over exactly the same `redactor/` package the CLI and the Streamlit app
use, so the three cannot drift apart.

Routes (file-based, so the path follows the filename):
    GET  /api/redact   -> capability probe: PII types and payload limits
    POST /api/redact   -> raw .docx bytes in, JSON out

The request body is the raw document rather than a multipart form. Multipart
would need `python-multipart` (or `cgi`, removed in Python 3.13) for no gain:
there is exactly one file and the options fit in the query string. Keeping the
dependency list at `python-docx` alone also keeps the bundle small.

The response carries the redacted document base64-encoded inside the JSON
because a Vercel Function gets one round trip -- there is no shared storage in
which to park the file for a follow-up request, and the stats and the surrogate
mapping are too large for response headers. See `MAX_RESPONSE_BYTES` for what
that costs.
"""

from __future__ import annotations

import base64
import json
import sys
import tempfile
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# The function's working directory is the project root, but the root is not
# guaranteed to be on sys.path when the handler is imported from `api/`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from redactor import PIIType, RedactionConfig, RedactionPipeline  # noqa: E402

#: Vercel caps both the request and the response body at 4.5 MB. The response
#: carries the redacted .docx base64-encoded (+33%) alongside the mapping, so
#: the *upload* ceiling is the binding one and sits well below 4.5 MB.
MAX_REQUEST_BYTES = 3 * 1024 * 1024
MAX_RESPONSE_BYTES = 4_400_000

#: Ceiling on how much of a rejected request body we will read before replying
#: (see `handler._drain`). Set just above the platform's own 4.5 MB cap.
MAX_DRAIN_BYTES = 5 * 1024 * 1024

#: spaCy is deliberately not in the Vercel dependency set, so the NER layer is
#: never available here. `build_ner_recognizers` already degrades gracefully;
#: this constant just lets the UI say so honestly instead of offering a toggle
#: that silently does nothing.
NER_AVAILABLE = False


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_options(query: str) -> tuple[RedactionConfig, bool]:
    """Build a RedactionConfig from the query string.

    Returns the config plus whether the caller asked for the surrogate mapping.
    Unknown type names are ignored rather than fatal, so a UI that is one
    version ahead of the API still works.
    """
    params = parse_qs(query)

    def first(key: str) -> str | None:
        values = params.get(key)
        return values[0] if values else None

    known = {t.value for t in PIIType}
    requested = first("types")
    if requested:
        selected = {PIIType(name) for name in requested.split(",") if name in known}
    else:
        selected = set(PIIType)
    if not selected:
        selected = set(PIIType)

    want_mapping = _truthy(first("mapping"), default=True)

    config = RedactionConfig(
        enabled_types=selected,
        use_ner=False,
        salt=first("salt") or "pii-redactor-v1",
        scrub_metadata=_truthy(first("metadata"), default=True),
        emit_mapping=want_mapping,
    )
    return config, want_mapping


def _redact(payload: bytes, config: RedactionConfig, want_mapping: bool) -> dict:
    """Run the pipeline over an in-memory .docx and return a JSON-ready dict."""
    # python-docx can read a file-like object, but `redact_docx` takes paths so
    # that the CLI, the Streamlit app and this handler all exercise the same
    # code path. /tmp is the only writable location in a Vercel Function.
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "input.docx"
        destination = Path(tmp) / "output.docx"
        source.write_bytes(payload)

        pipeline = RedactionPipeline(config)
        result = pipeline.redact_docx(source, destination)
        redacted = destination.read_bytes()

    body = {
        "ok": True,
        "blocks_processed": result.blocks_processed,
        "entities_redacted": result.stats.total(),
        "counts_by_type": result.stats.counts,
        "metadata_fields_scrubbed": result.metadata_fields_scrubbed,
        "ner_active": result.ner_active,
        "docx_base64": base64.b64encode(redacted).decode("ascii"),
    }
    if want_mapping:
        body["mapping"] = result.mapping
    return body


class handler(BaseHTTPRequestHandler):
    """Vercel loads the class named `handler` from each file under `api/`."""

    # Silence per-request logging to stderr; Vercel captures it as noise.
    def log_message(self, fmt, *args):  # noqa: A003 - signature fixed by base class
        pass

    # -- helpers ------------------------------------------------------------

    def _send_json(self, status: int, body: dict) -> None:
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_error(self, status: int, message: str, **extra) -> None:
        self._send_json(status, {"ok": False, "error": message, **extra})

    def _drain(self, length: int) -> None:
        """Read and discard a request body we are about to reject.

        Replying before the client has finished sending makes the peer see a
        reset connection rather than the response, so the caller gets an opaque
        network error instead of the explanation. Bounded by the platform's
        4.5 MB request cap, so this cannot be made to read unboundedly.
        """
        remaining = min(length, MAX_DRAIN_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    # -- routes -------------------------------------------------------------

    def do_GET(self) -> None:
        """Capability probe, so the page renders the real type list."""
        self._send_json(
            200,
            {
                "ok": True,
                "types": [t.value for t in PIIType],
                "max_upload_bytes": MAX_REQUEST_BYTES,
                "ner_available": NER_AVAILABLE,
            },
        )

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_error(400, "Malformed Content-Length header.")
            return

        if length <= 0:
            self._send_error(400, "Empty request body -- send the .docx bytes as the body.")
            return

        if length > MAX_REQUEST_BYTES:
            self._drain(length)
            self._send_error(
                413,
                f"That file is {length / 1_048_576:.1f} MB. Vercel caps a function's "
                f"request and response at 4.5 MB, and the redacted document is "
                f"returned base64-encoded in the response, so uploads here are "
                f"limited to {MAX_REQUEST_BYTES // 1_048_576} MB. Use the Streamlit "
                f"deployment on Render for larger documents.",
                limit_bytes=MAX_REQUEST_BYTES,
            )
            return

        payload = self.rfile.read(length)

        # .docx is a ZIP container; every one starts "PK\x03\x04". Checking here
        # turns a confusing python-docx traceback into a clear message.
        if not payload.startswith(b"PK\x03\x04"):
            self._send_error(400, "That does not look like a .docx file (bad ZIP header).")
            return

        config, want_mapping = _parse_options(urlparse(self.path).query)

        try:
            body = _redact(payload, config, want_mapping)
        except Exception as exc:  # surface the reason instead of a bare 500
            self._send_error(500, f"Redaction failed: {type(exc).__name__}: {exc}")
            return

        encoded_size = len(json.dumps(body).encode("utf-8"))
        if encoded_size > MAX_RESPONSE_BYTES:
            # Retry without the mapping, which is the only part we can drop
            # without withholding the document itself.
            body.pop("mapping", None)
            body["mapping_omitted"] = "Response too large; the surrogate mapping was dropped."
            encoded_size = len(json.dumps(body).encode("utf-8"))

        if encoded_size > MAX_RESPONSE_BYTES:
            self._send_error(
                413,
                "The redacted document is too large to return through a Vercel "
                "Function (4.5 MB response cap). Use the Streamlit deployment on "
                "Render for this file.",
            )
            return

        self._send_json(200, body)
