# Deploying

Two deployments, from one repository, sharing one engine.

| | Render | Vercel |
|---|---|---|
| What runs | `app.py` — the full Streamlit UI | `public/index.html` + `api/redact.py` |
| Upload limit | 25 MB | **3 MB** |
| Redaction engine | `redactor/` | `redactor/` — the same package |
| Cold start | ~50s on the free plan (spins down when idle) | ~1s |
| spaCy NER | not installed (512 MB RAM) | not installed (keeps the bundle small) |

Both run the deterministic recognizers only, which is the configuration the
numbers in [EVALUATION.md](EVALUATION.md) were produced under.

---

## Why the split

Streamlit holds a WebSocket open for the lifetime of a session and keeps
per-session state in the server process. A Vercel Function is a request-scoped
lambda that is frozen between invocations, so `streamlit run` cannot be the
Vercel entrypoint — not as a configuration detail, but structurally.

So Vercel gets a surface built for it: a static page plus one HTTP function
that imports the same `redactor/` package. All detection logic stays in that
package, so the CLI, the Streamlit app and the serverless function cannot
drift apart.

The tradeoff Vercel imposes in return is the payload cap: **4.5 MB for the
request and 4.5 MB for the response**, and the redacted document has to come
back inside the response because a function has no shared storage in which to
park it for a follow-up request. Base64 costs another 33%, which is what puts
the upload ceiling at 3 MB. Measured on the 1.8 MB, 4,027-block prospectus:
17.3s, a 2.40 MB response, 2.1 MB of headroom. Anything larger belongs on the
Render deployment.

---

## 1. Push to GitHub

Both platforms deploy from a repository.

```bash
cd pii-redactor
git init -b main
git add .
git commit -m "PII redaction tool"
gh repo create pii-redactor --public --source=. --push
```

Without the `gh` CLI, create an empty repo on github.com and:

```bash
git remote add origin https://github.com/<you>/pii-redactor.git
git push -u origin main
```

> The repo root must be `pii-redactor/` — the directory holding `render.yaml`
> and `vercel.json`. If you push the parent directory instead, set each
> platform's **Root Directory** to `pii-redactor`.

---

## 2. Render — the Streamlit app

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**
2. Connect the repository. Render reads [`render.yaml`](render.yaml) and fills
   everything in — build command, start command, health check, Python version.
3. **Apply**. First build takes 2–4 minutes.

You get `https://pii-redaction-tool.onrender.com` (Render appends a suffix if
the name is taken).

What `render.yaml` sets, and why:

- `startCommand` passes `--server.address 0.0.0.0`. Streamlit binds to
  localhost by default and Render routes to the container's external
  interface, so without this the service builds fine and then fails every
  health check.
- `healthCheckPath: /_stcore/health` is Streamlit's own liveness endpoint.
  Probing `/` instead returns the app shell before the server is ready.
- `$PORT` is injected by Render and must not be hardcoded.

**Free plan caveat:** the service spins down after 15 minutes idle, and the
next request pays a ~50 second cold start. That is the plan working as
designed, not a fault — but do not open the link cold in front of an audience.

---

## 3. Vercel — the upload page and function

1. [vercel.com/new](https://vercel.com/new) → import the same repository
2. Framework preset: **Other**. Leave build and output settings alone —
   [`vercel.json`](vercel.json) sets `outputDirectory` to `public`.
3. **Deploy**. Takes under a minute; only `python-docx` is installed.

Three pieces of configuration are load-bearing:

- **[`.vercelignore`](.vercelignore) excludes `app.py`.** Vercel's Python
  runtime treats a root-level `app.py` as the application entrypoint and
  expects an ASGI/WSGI `app` object in it. Ours is the Streamlit script, so
  leaving it visible makes Vercel try to serve Streamlit as a framework preset
  — and a detected preset takes precedence over file-based functions in
  `api/`, so `api/redact.py` would never be routed. Excluding it is what makes
  the deployment resolve correctly.
- **`requirements.txt` is core-only** (`python-docx`). Vercel installs the root
  requirements file into the bundle; the Streamlit and spaCy extras would add
  roughly 250 MB of dependencies that the function never imports. They live in
  `requirements-app.txt` and `requirements-ner.txt`, both `.vercelignore`d.
- **`maxDuration: 300`** in `vercel.json`. The default on legacy (non-Fluid)
  projects is 10 seconds, and a full prospectus takes ~17. New Vercel projects
  have Fluid compute on by default, where 300s is both the Hobby default and
  its maximum. *If the build rejects the value*, Fluid compute is off for the
  project: turn it on in **Settings → Functions**, or lower the value to `60`,
  which is the legacy Hobby ceiling and still ample.

### Checking it works

```bash
curl https://<your-project>.vercel.app/api/redact
# {"ok": true, "types": [...15 types...], "max_upload_bytes": 3145728, ...}

curl -X POST --data-binary @data/synthetic_pii.docx \
  "https://<your-project>.vercel.app/api/redact?types=PERSON,EMAIL&mapping=1" \
  | head -c 400
```

### The API

`POST /api/redact` — raw `.docx` bytes as the body, options in the query
string. Not multipart: there is one file and the options are scalars, so
multipart would only add a `python-multipart` dependency.

| Parameter | Default | Meaning |
|---|---|---|
| `types` | all | Comma-separated `PIIType` names. Unknown names are ignored, not fatal. |
| `salt` | `pii-redactor-v1` | Surrogate salt. Same salt ⇒ byte-identical output. |
| `metadata` | `1` | Scrub author/company/last-modified-by. |
| `mapping` | `1` | Include the real→fake table in the response. |

Returns `{ok, blocks_processed, entities_redacted, counts_by_type,
metadata_fields_scrubbed, ner_active, mapping, docx_base64}`.

Errors return `{ok: false, error}` with a real status: `400` for a body that
is not a `.docx`, `413` over the size cap, `500` with the exception type for a
redaction failure.

> `mapping` re-identifies the document. The page keeps it behind a collapsed
> warning and excludes it from the downloadable audit log, matching the CLI's
> `--mapping` behaviour. Pass `mapping=0` to never generate it.

---

## Local checks before pushing

```bash
pip install -r requirements-app.txt
python -m pytest tests/ -q          # 24 tests
python cli.py data/synthetic_pii.docx -o /tmp/out.docx
streamlit run app.py                # the Render surface, locally
```

There is no local runner for the Vercel surface without the Vercel CLI
(`npx vercel dev`), but `api/redact.py` is a plain `BaseHTTPRequestHandler`,
so it can be served by the standard library:

```python
from http.server import HTTPServer
import sys; sys.path.insert(0, "api")
import redact
HTTPServer(("127.0.0.1", 8000), redact.handler).serve_forever()
# then open public/index.html against http://127.0.0.1:8000
```
